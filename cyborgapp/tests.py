from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from cyborgapp.models import (
    Category, SubCategory, CustomerRequirement, RequirementItem,
    DistrictRequirementAssignment, RequirementAssignment, Lead, LeadItem,
    LeadInstallment, WithdrawalRequest, Notification
)
from decimal import Decimal
from cyborgapp.views import check_assignment_limits

CustomUser = get_user_model()

class RequirementAssignmentTest(TestCase):
    def setUp(self):
        # Create users
        self.superadmin = CustomUser.objects.create_superuser(
            username='admin', email='admin@test.com', password='password123', usertype='superadmin'
        )
        self.customer = CustomUser.objects.create_user(
            username='customer_user', email='customer@test.com', password='password123', usertype='customer'
        )
        self.district = CustomUser.objects.create_user(
            username='dist_user', email='dist@test.com', password='password123', usertype='district'
        )
        self.manager = CustomUser.objects.create_user(
            username='manager_user', email='manager@test.com', password='password123', usertype='manager',
            assigned_district=self.district
        )
        self.mandalam = CustomUser.objects.create_user(
            username='mand_user', email='mand@test.com', password='password123', usertype='mandalam',
            assigned_district=self.district
        )
        self.marketing = CustomUser.objects.create_user(
            username='mark_user', email='mark@test.com', password='password123', usertype='marketing',
            assigned_district=self.district, assigned_mandalam=self.mandalam
        )

        # Category and SubCategory
        self.category = Category.objects.create(name='Services', cat_type='count', created_by=self.superadmin)
        self.subcategory = SubCategory.objects.create(category=self.category, name='SubService', created_by=self.superadmin)

        # Customer Requirement
        self.requirement = CustomerRequirement.objects.create(
            customer=self.customer, category=self.category, title='Need Services', status='approved'
        )
        self.requirement.customer.accessible_districts.add(self.district)

        # Requirement Item
        self.item = RequirementItem.objects.create(
            requirement=self.requirement, subcategory=self.subcategory, count=100,
            customer_amount=Decimal('50.00'), admin_markup=Decimal('10.00'), other_expenses=Decimal('5.00'), gst=Decimal('18.00')
        )

    def test_district_assignment(self):
        # Superadmin assigns count to District
        dist_assignment = DistrictRequirementAssignment.objects.create(
            requirement_item=self.item, district=self.district, assigned_count=50, assigned_by=self.superadmin
        )
        self.assertEqual(dist_assignment.assigned_count, 50)
        self.assertEqual(self.item.get_total_assigned_count, 50)
        self.assertEqual(self.item.get_remaining_assignable_count, 50)

    def test_mandalam_assignment_limits(self):
        # No DistrictRequirementAssignment needed anymore.
        # District assigns directly to FCs; limit is item.count (set by associate company = 100)

        client = Client()
        client.login(username='dist_user', password='password123')

        # Test assign_mandalams GET - district_available should equal item.count (100) since nothing assigned yet
        response = client.get(f'/requirements/item/{self.item.id}/assign-mandalams/')
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertEqual(res_data['status'], 'success')
        self.assertEqual(res_data['district_available'], 100)  # item.count = 100, none assigned yet

        # Test assign_mandalams POST exceeding item count (110 > 100)
        post_data = {
            'mandalams': [self.mandalam.id],
            f'count_{self.mandalam.id}': 110
        }
        response = client.post(f'/requirements/item/{self.item.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 400)

        # Test assign_mandalams POST valid (60 <= 100)
        post_data = {
            'mandalams': [self.mandalam.id],
            f'count_{self.mandalam.id}': 60
        }
        response = client.post(f'/requirements/item/{self.item.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 200)

        # Check RequirementAssignment was created
        fc_assignment = RequirementAssignment.objects.get(requirement_item=self.item, facilitation_center=self.mandalam)
        self.assertEqual(fc_assignment.assigned_count, 60)

        # GET again - district_available should now reflect remaining available count after subtracting what has been assigned to this district's FCs
        # (district_available = item_count - total_already_assigned = 100 - 60 = 40)
        response = client.get(f'/requirements/item/{self.item.id}/assign-mandalams/')
        res_data = response.json()
        self.assertEqual(res_data['district_available'], 40)

    def test_lead_creation_and_limits(self):
        # Set up FC assignment (no DistrictRequirementAssignment needed anymore)
        RequirementAssignment.objects.create(
            requirement_item=self.item, facilitation_center=self.mandalam, assigned_count=30, assigned_by=self.district
        )

        # Login as marketing user
        client = Client()
        client.login(username='mark_user', password='password123')

        # Test lead_create post exceeding facilitation center limit (35 > 30)
        post_data = {
            'name': 'Test Client',
            'phone': '9876543210',
            'email': 'client@test.com',
            'address': 'Test Address',
            'remarks': 'Test Remarks',
            'selected_items': [self.subcategory.id],
            f'count_{self.subcategory.id}': 35
        }
        response = client.post(f'/requirements/{self.requirement.id}/lead/create/', post_data)
        # Should redirect back to detail with an error message
        self.assertEqual(response.status_code, 302)
        
        # Verify no lead was created
        self.assertFalse(Lead.objects.exists())

        # Test lead_create post valid limit (20 <= 30)
        post_data[f'count_{self.subcategory.id}'] = 20
        response = client.post(f'/requirements/{self.requirement.id}/lead/create/', post_data)
        self.assertEqual(response.status_code, 302)
        
        # Verify lead created
        self.assertTrue(Lead.objects.exists())
        lead = Lead.objects.first()
        self.assertEqual(lead.status, 'pending')

        # Check check_assignment_limits directly
        self.assertIsNone(check_assignment_limits(lead))

        # Check check_assignment_limits if requested exceeds left count (e.g. if we increase count directly to 40)
        lead_item = lead.items.first()
        lead_item.count = 40
        lead_item.save()
        
        # Now check_assignment_limits should return error string
        error_msg = check_assignment_limits(lead)
        self.assertIsNotNone(error_msg)
        self.assertIn("Insufficient assigned count", error_msg)

    def test_lead_creation_and_edit_when_remaining_is_zero(self):
        # Set up FC assignment with assigned_count = 0 (remaining limit is 0)
        RequirementAssignment.objects.create(
            requirement_item=self.item, facilitation_center=self.mandalam, assigned_count=0, assigned_by=self.district
        )

        client = Client()
        client.login(username='mark_user', password='password123')

        # Try to create a lead with qty 1 when remaining count is 0
        post_data = {
            'name': 'Test Client Zero',
            'phone': '9876543210',
            'email': 'client_zero@test.com',
            'address': 'Test Address',
            'remarks': 'Test Remarks',
            'selected_items': [self.subcategory.id],
            f'count_{self.subcategory.id}': 1
        }
        response = client.post(f'/requirements/{self.requirement.id}/lead/create/', post_data)
        self.assertEqual(response.status_code, 302)
        # Verify no lead was created since remaining limit was 0
        self.assertFalse(Lead.objects.filter(name='Test Client Zero').exists())

    def test_mandalam_assignment_cannot_be_less_than_sold(self):
        # Set up FC assignment with assigned_count = 10
        fc_asgn = RequirementAssignment.objects.create(
            requirement_item=self.item, facilitation_center=self.mandalam, assigned_count=10, assigned_by=self.district
        )

        # Create a lead with status='confirmed' to consume 3 items
        lead = Lead.objects.create(
            requirement=self.requirement, marketing_user=self.marketing, status='confirmed'
        )
        LeadItem.objects.create(lead=lead, subcategory=self.subcategory, count=3)

        self.assertEqual(fc_asgn.get_sold_count, 3)

        client = Client()
        client.login(username='dist_user', password='password123')

        # Test 1: Try to decrease count below sold (e.g. to 2 < 3)
        post_data = {
            'mandalams': [self.mandalam.id],
            f'count_{self.mandalam.id}': 2
        }
        response = client.post(f'/requirements/item/{self.item.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 400)
        res_data = response.json()
        self.assertEqual(res_data['status'], 'error')
        self.assertIn("Cannot decrease count", res_data['message'])

        # Test 2: Try to remove/uncheck mandalam entirely when sold > 0
        post_data = {
            'mandalams': []  # uncheck mandalam
        }
        response = client.post(f'/requirements/item/{self.item.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 400)
        res_data = response.json()
        self.assertEqual(res_data['status'], 'error')
        self.assertIn("Cannot remove Facilitation Center", res_data['message'])

        # Test 3: Valid change (decrease to 5 >= 3)
        post_data = {
            'mandalams': [self.mandalam.id],
            f'count_{self.mandalam.id}': 5
        }
        response = client.post(f'/requirements/item/{self.item.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 200)
        fc_asgn.refresh_from_db()
        self.assertEqual(fc_asgn.assigned_count, 5)

    def test_mandalam_assignment_cannot_be_less_than_sold_after_reapprove(self):
        # Set up FC assignment with assigned_count = 10
        fc_asgn = RequirementAssignment.objects.create(
            requirement_item=self.item, facilitation_center=self.mandalam, assigned_count=10, assigned_by=self.district
        )

        # Create a lead with status='confirmed' to consume 3 items
        lead = Lead.objects.create(
            requirement=self.requirement, marketing_user=self.marketing, status='confirmed'
        )
        LeadItem.objects.create(lead=lead, subcategory=self.subcategory, count=3)

        # Now simulate requirement moving to pending (deletes the assignment) and approved again
        self.requirement.status = 'pending'
        self.requirement.save()
        # Trigger cleanup
        RequirementAssignment.objects.filter(requirement_item__requirement=self.requirement).delete()
        
        # Verify assignment was deleted
        self.assertFalse(RequirementAssignment.objects.filter(id=fc_asgn.id).exists())

        # Re-approve requirement
        self.requirement.status = 'approved'
        self.requirement.save()

        # Login as district user
        client = Client()
        client.login(username='dist_user', password='password123')

        # Try to assign count to FC below sold (e.g. 2 < 3)
        post_data = {
            'mandalams': [self.mandalam.id],
            f'count_{self.mandalam.id}': 2
        }
        response = client.post(f'/requirements/item/{self.item.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 400)
        res_data = response.json()
        self.assertEqual(res_data['status'], 'error')
        self.assertIn("Cannot decrease count", res_data['message'])

        # Try to remove FC from checked mandalams
        post_data = {
            'mandalams': []
        }
        response = client.post(f'/requirements/item/{self.item.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 400)
        res_data = response.json()
        self.assertEqual(res_data['status'], 'error')
        self.assertIn("Cannot remove Facilitation Center", res_data['message'])

    def test_noncount_lead_confirm_blocked_when_remaining_zero(self):
        """For non-count categories, confirm must be blocked when FC remaining limit is 0."""
        from decimal import Decimal
        # Create a non-count category/subcategory/requirement/item
        other_cat = Category.objects.create(name='OtherCat', cat_type='other', created_by=self.superadmin)
        other_sub = SubCategory.objects.create(category=other_cat, name='OtherSub', created_by=self.superadmin)
        other_req = CustomerRequirement.objects.create(
            customer=self.customer, category=other_cat, title='Other Req', status='approved'
        )
        other_req.customer.accessible_districts.add(self.district)
        other_item = RequirementItem.objects.create(
            requirement=other_req, subcategory=other_sub, count=5,
            customer_amount=Decimal('100.00'), admin_markup=Decimal('10.00'),
            other_expenses=Decimal('0.00'), gst=Decimal('0.00')
        )

        # Assign FC with count = 1
        fc_asgn = RequirementAssignment.objects.create(
            requirement_item=other_item, facilitation_center=self.mandalam,
            assigned_count=1, assigned_by=self.district
        )

        # Consume the 1 slot with a confirmed lead
        existing_lead = Lead.objects.create(
            requirement=other_req, marketing_user=self.marketing, status='confirmed'
        )
        LeadItem.objects.create(lead=existing_lead, subcategory=other_sub, count=0)

        # fc_asgn now has 0 remaining (get_sold_count = 1, assigned_count = 1)
        self.assertEqual(fc_asgn.get_sold_count, 1)
        self.assertEqual(fc_asgn.get_left_count, 0)

        # Now try to confirm a NEW pending lead for the same subcategory
        new_lead = Lead.objects.create(
            requirement=other_req, marketing_user=self.marketing, status='pending'
        )
        LeadItem.objects.create(lead=new_lead, subcategory=other_sub, count=0)

        # check_assignment_limits should return an error
        error = check_assignment_limits(new_lead)
        self.assertIsNotNone(error)
        self.assertIn('Insufficient assigned count', error)

    def test_manager_assignment_functionality(self):
        """Verify that a manager can assign requirements/counts to facilitation centers under their district."""
        client = Client()
        client.login(username='manager_user', password='password123')

        # Try to assign count to FC (e.g. 5)
        post_data = {
            'mandalams': [self.mandalam.id],
            f'count_{self.mandalam.id}': 5
        }
        response = client.post(f'/requirements/item/{self.item.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 200)

        # Verify the assignment was successfully created
        from cyborgapp.models import RequirementAssignment
        fc_asgn = RequirementAssignment.objects.filter(
            requirement_item=self.item, facilitation_center=self.mandalam
        ).first()
        self.assertIsNotNone(fc_asgn)
        self.assertEqual(fc_asgn.assigned_count, 5)

    def test_lead_part_payment_and_installments(self):
        """Verify single vs part payment creation, validations, and installment collection workflow."""
        from cyborgapp.models import Lead, LeadInstallment
        from decimal import Decimal
        import json

        # Create a fresh subcategory + item with clean amounts (no GST, no markup)
        # so total_amount = customer_amount = 1000 exactly (count-type * count=1)
        clean_sub = SubCategory.objects.create(
            category=self.category, name='CleanSub', created_by=self.superadmin
        )
        clean_item = RequirementItem.objects.create(
            requirement=self.requirement,
            subcategory=clean_sub,
            count=5,
            customer_amount=Decimal('1000.00'),
            admin_markup=Decimal('0.00'),
            other_expenses=Decimal('0.00'),
            gst=Decimal('0.00')
        )

        # Set up FC assignment with sufficient slot count for the clean item
        RequirementAssignment.objects.create(
            requirement_item=clean_item, facilitation_center=self.mandalam,
            assigned_count=5, assigned_by=self.district
        )

        # Create a pending lead using the clean subcategory (count=1 → total=1000)
        lead = Lead.objects.create(
            requirement=self.requirement,
            marketing_user=self.marketing,
            name='Part Payment Client',
            phone='9876543210',
            status='pending',
            current_level='superadmin',   # lead has been escalated to superadmin for confirmation
        )
        LeadItem.objects.create(lead=lead, subcategory=clean_sub, count=1)

        # Ensure requirement is approved so updating is allowed
        self.requirement.status = 'approved'
        self.requirement.save()


        # Login as superadmin to confirm the lead
        client = Client()
        client.login(username='admin', password='password123')

        # 1. Verify fetch lead updates returns total_amount=1000 and no installments
        response = client.get(f'/leads/{lead.id}/updates/get/')
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertEqual(res_data['status'], 'success')
        self.assertAlmostEqual(res_data['total_amount'], 1000.00, places=2)
        self.assertEqual(res_data['payment_mode'], 'single')
        self.assertEqual(len(res_data['installments']), 0)

        # 2. Test Part Payment with mismatched sum (400+400=800 ≠ 1000)
        from django.utils import timezone
        import datetime
        today = timezone.localtime(timezone.now()).date()
        today_str = today.strftime('%Y-%m-%d')
        tomorrow_str = (today + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        day_after_str = (today + datetime.timedelta(days=2)).strftime('%Y-%m-%d')

        post_data = {
            'update_text': 'Confirming lead with split payment',
            'status': 'confirmed',
            'pass_lead': False,
            'payment_mode': 'part',
            'installments': [400.00, 400.00],
            'installment_dates': [today_str, tomorrow_str]
        }
        response = client.post(
            f'/leads/{lead.id}/update/',
            data=json.dumps(post_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('must equal the total lead amount', response.json()['message'])

        # 2a. Test Part Payment with missing installment_dates
        invalid_post_data = {
            'update_text': 'Confirming lead with split payment',
            'status': 'confirmed',
            'pass_lead': False,
            'payment_mode': 'part',
            'installments': [400.00, 300.00, 300.00]
        }
        response = client.post(
            f'/leads/{lead.id}/update/',
            data=json.dumps(invalid_post_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Installment dates are mandatory', response.json()['message'])

        # 2b. Test Part Payment with mismatched installment_dates length
        invalid_post_data['installment_dates'] = [today_str, tomorrow_str]
        response = client.post(
            f'/leads/{lead.id}/update/',
            data=json.dumps(invalid_post_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Installment dates are mandatory', response.json()['message'])

        # 2c. Test Part Payment with out-of-order installment_dates
        invalid_post_data['installment_dates'] = [tomorrow_str, today_str, day_after_str]
        response = client.post(
            f'/leads/{lead.id}/update/',
            data=json.dumps(invalid_post_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('cannot be earlier than', response.json()['message'])

        # 2d. Test Part Payment where first installment is not today's date
        invalid_post_data['installment_dates'] = [tomorrow_str, tomorrow_str, day_after_str]
        response = client.post(
            f'/leads/{lead.id}/update/',
            data=json.dumps(invalid_post_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("First installment date must be today's date", response.json()['message'])

        # 3. Part Payment with correct sum and dates: [400, 300, 300] = 1000
        post_data['installments'] = [400.00, 300.00, 300.00]
        post_data['installment_dates'] = [today_str, tomorrow_str, day_after_str]
        response = client.post(
            f'/leads/{lead.id}/update/',
            data=json.dumps(post_data),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        res_json = response.json()
        self.assertEqual(res_json['status'], 'payment_required')
        # First installment is 400 → 40000 paise
        self.assertEqual(res_json['amount'], 40000)

        # DB should have 3 installments all pending, lead.payment_mode='part'
        lead.refresh_from_db()
        self.assertEqual(lead.payment_mode, 'part')
        installments = lead.installments.all().order_by('installment_number')
        self.assertEqual(installments.count(), 3)
        self.assertEqual(installments[0].amount, Decimal('400.00'))
        self.assertEqual(installments[0].status, 'pending')
        self.assertEqual(str(installments[0].due_date), today_str)
        self.assertEqual(str(installments[1].due_date), tomorrow_str)
        self.assertEqual(str(installments[2].due_date), day_after_str)

        # 4. Simulate first installment payment verification (mock Razorpay)
        from unittest.mock import patch
        with patch('razorpay.Client') as mock_razorpay:
            instance = mock_razorpay.return_value
            instance.utility.verify_payment_signature.return_value = True

            post_data_verify = dict(post_data)
            post_data_verify.update({
                'razorpay_payment_id': 'pay_mock_111',
                'razorpay_order_id': 'order_mock_111',
                'razorpay_signature': 'sig_mock_111'
            })
            response = client.post(
                f'/leads/{lead.id}/update/',
                data=json.dumps(post_data_verify),
                content_type='application/json'
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'success')

        # Lead is now confirmed; first installment is paid
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'confirmed')
        self.assertEqual(lead.razorpay_payment_id, 'pay_mock_111')

        inst1 = lead.installments.get(installment_number=1)
        self.assertEqual(inst1.status, 'paid')
        self.assertEqual(inst1.razorpay_payment_id, 'pay_mock_111')

        inst2 = lead.installments.get(installment_number=2)
        inst3 = lead.installments.get(installment_number=3)
        self.assertEqual(inst2.status, 'pending')
        self.assertEqual(inst3.status, 'pending')

        # 5. lead_get_updates should now list all 3 installments with correct statuses
        response = client.get(f'/leads/{lead.id}/updates/get/')
        res_data = response.json()
        self.assertEqual(len(res_data['installments']), 3)
        self.assertEqual(res_data['installments'][0]['status'], 'paid')
        self.assertEqual(res_data['installments'][1]['status'], 'pending')

        # 6. Out-of-order payment: paying inst3 before inst2 must fail
        response = client.get(f'/installments/{inst3.id}/pay/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('pay the previous installments first', response.json()['message'])

        # 7. Paying inst2 (the next in sequence) must succeed
        with patch('razorpay.Client') as mock_razorpay:
            instance = mock_razorpay.return_value
            instance.order.create.return_value = {
                'id': 'order_mock_222', 'amount': 30000, 'currency': 'INR'
            }
            response = client.get(f'/installments/{inst2.id}/pay/')
            self.assertEqual(response.status_code, 200)
            res_json = response.json()
            self.assertEqual(res_json['status'], 'payment_required')
            # inst2 amount = 300 → 30000 paise
            self.assertEqual(res_json['amount'], 30000)

        # 8. Verify payment for inst2
        with patch('razorpay.Client') as mock_razorpay:
            instance = mock_razorpay.return_value
            instance.utility.verify_payment_signature.return_value = True

            response = client.post(
                f'/installments/{inst2.id}/verify/',
                data=json.dumps({
                    'razorpay_payment_id': 'pay_mock_222',
                    'razorpay_order_id': 'order_mock_222',
                    'razorpay_signature': 'sig_mock_222'
                }),
                content_type='application/json'
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'success')

        inst2.refresh_from_db()
        self.assertEqual(inst2.status, 'paid')
        self.assertEqual(inst2.razorpay_payment_id, 'pay_mock_222')


class GSTAndExpensesTestCase(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username='superadmin@cyborg.com',
            email='superadmin@cyborg.com',
            name='Super Admin',
            usertype='superadmin',
            password='password123'
        )
        self.category = Category.objects.create(name='Test Category', cat_type='fixed')
        self.subcategory = SubCategory.objects.create(category=self.category, name='Test Sub')
        self.customer = CustomUser.objects.create_user(
            username='customer@cyborg.com',
            email='customer@cyborg.com',
            name='Customer',
            usertype='customer',
            password='password123'
        )
        self.marketing_user = CustomUser.objects.create_user(
            username='marketing@cyborg.com',
            email='marketing@cyborg.com',
            name='Marketing User',
            usertype='marketing',
            password='password123'
        )
        self.requirement = CustomerRequirement.objects.create(
            customer=self.customer,
            category=self.category,
            title='Test Req',
            customer_amount=Decimal('1000.00'),
            admin_markup=Decimal('200.00'),
            other_expenses=Decimal('100.00'),
            gst=Decimal('18.00'),
            status='approved'
        )
        # Create requirement item
        self.req_item = RequirementItem.objects.create(
            requirement=self.requirement,
            subcategory=self.subcategory,
            customer_amount=Decimal('1000.00'),
            admin_markup=Decimal('200.00'),
            other_expenses=Decimal('100.00'),
            gst=Decimal('18.00')
        )
        
        self.lead = Lead.objects.create(
            requirement=self.requirement,
            marketing_user=self.marketing_user,
            status='confirmed',
            total_amount=Decimal('1534.00'),
            payment_mode='full'
        )
        self.lead_item = LeadItem.objects.create(
            lead=self.lead,
            subcategory=self.subcategory,
            count=1
        )

    def test_gst_and_expense_properties(self):
        # Base = 1000 + 200 + 100 = 1300. GST = 1300 * 0.18 = 234. Total = 1534.
        self.assertEqual(self.lead.get_expense_amount, Decimal('100.00'))
        self.assertEqual(self.lead.get_gst_amount, Decimal('234.00'))

    def test_gst_dashboard_access_and_withdrawal(self):
        client = Client()
        client.login(username='superadmin@cyborg.com', password='password123')
        
        response = client.get('/superadmin/gst/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'GST Balance')
        self.assertContains(response, '234.00')

        # Request GST withdrawal
        response = client.post('/superadmin/gst/withdraw/', {
            'amount': '100.00',
            'account_number': '1234567890',
            'ifsc_code': 'ABCD0123456',
            'account_holder': 'Super Admin',
            'phone_linked': '9876543210'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify withdrawal request was created and directly approved
        wr = WithdrawalRequest.objects.get(request_type='gst')
        self.assertEqual(wr.amount, Decimal('100.00'))
        self.assertEqual(wr.status, 'approved')

    def test_expenses_dashboard_access_and_withdrawal(self):
        client = Client()
        client.login(username='superadmin@cyborg.com', password='password123')
        
        response = client.get('/superadmin/expenses/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Expenses Balance')
        self.assertContains(response, '100.00')

        # Request Expense withdrawal
        response = client.post('/superadmin/expenses/withdraw/', {
            'amount': '50.00',
            'account_number': '1234567890',
            'ifsc_code': 'ABCD0123456',
            'account_holder': 'Super Admin',
            'phone_linked': '9876543210'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify withdrawal request was created and directly approved
        wr = WithdrawalRequest.objects.get(request_type='expense')
        self.assertEqual(wr.amount, Decimal('50.00'))
        self.assertEqual(wr.status, 'approved')

    def test_associate_wallets_access(self):
        client = Client()
        client.login(username='superadmin@cyborg.com', password='password123')
        
        response = client.get('/superadmin/associate-wallets/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Company Financial Status')
        self.assertContains(response, 'Associate Wallets Management')

    def test_navbar_wallet_pending_counts(self):
        # Create non-associate user pending request
        wr_user = WithdrawalRequest.objects.create(
            user=self.marketing_user,
            amount=Decimal('500.00'),
            account_number='654321',
            ifsc_code='TEST0002',
            account_holder='Mkt User',
            status='pending',
            request_type='wallet'
        )
        # Create associate pending request
        wr_assoc = WithdrawalRequest.objects.create(
            user=self.customer,
            amount=Decimal('100.00'),
            account_number='123456',
            ifsc_code='TEST0001',
            account_holder='Assoc',
            status='pending',
            request_type='wallet'
        )
        client = Client()
        client.login(username='superadmin@cyborg.com', password='password123')
        res = client.get('/superadmin/associate-wallets/')
        self.assertEqual(res.context['global_pending_withdrawals_count'], 1)
        self.assertEqual(res.context['global_pending_associate_withdrawals_count'], 1)

    def test_customer_wallet_dashboard_cards(self):
        client = Client()
        client.login(username='customer@cyborg.com', password='password123')
        
        response = client.get('/wallet/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Current Balance')
        self.assertContains(response, 'Total Withdrawal Earned')
        self.assertContains(response, 'Total Earned')
        self.assertContains(response, 'Pending Balance')
        self.assertContains(response, 'View All Requests')
        self.assertNotContains(response, 'Total Transactions')


class SuperadminLeaderboardsTestCase(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username='superadmin_lb@cyborg.com',
            email='superadmin_lb@cyborg.com',
            name='Super Admin Leaderboard',
            usertype='superadmin',
            password='password123'
        )
        self.marketing_user = CustomUser.objects.create_user(
            username='m_lb@cyborg.com',
            email='m_lb@cyborg.com',
            name='Marketing User LB',
            usertype='marketing',
            password='password123'
        )
        self.category = Category.objects.create(name='Test Category LB', cat_type='fixed')
        self.requirement = CustomerRequirement.objects.create(
            customer=self.superadmin,
            category=self.category,
            title='LB Req',
            customer_amount=Decimal('1000.00'),
            admin_markup=Decimal('200.00'),
            other_expenses=Decimal('100.00'),
            gst=Decimal('18.00'),
            status='approved'
        )
        self.lead = Lead.objects.create(
            requirement=self.requirement,
            marketing_user=self.marketing_user,
            status='confirmed',
            total_amount=Decimal('1534.00'),
            payment_mode='single'
        )

    def test_leaderboard_access_and_filtering(self):
        client = Client()
        client.login(username='superadmin_lb@cyborg.com', password='password123')
        
        response = client.get('/superadmin/leaderboards/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Marketing User LB')
        self.assertContains(response, '1534.00')

        # Test filtering
        response = client.get('/superadmin/leaderboards/?metric=count')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Marketing User LB')

    def test_leads_and_confirmed_leads_export(self):
        client = Client()
        client.login(username='superadmin_lb@cyborg.com', password='password123')
        
        # Test active leads export
        response = client.get('/superadmin/leads/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertTrue('attachment' in response['Content-Disposition'])
        
        # Test search filter parameter
        response_search = client.get('/superadmin/leads/export/?search=Lead')
        self.assertEqual(response_search.status_code, 200)
        
        # Test empty check with search filter
        response_empty = client.get('/superadmin/leads/export/?check_empty=true&search=NonExistentQueryXYZ')
        self.assertEqual(response_empty.status_code, 200)
        self.assertEqual(response_empty.json(), {'empty': True})
        
        # Test confirmed leads export
        response = client.get('/superadmin/confirmed-leads/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertTrue('attachment' in response['Content-Disposition'])

    def test_withdrawal_requests_export(self):
        client = Client()
        client.login(username='superadmin_lb@cyborg.com', password='password123')
        
        response = client.get('/superadmin/withdrawal-requests/export-csv/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertTrue('attachment' in response['Content-Disposition'])


class MultipleMandatoryMilestoneTest(TestCase):
    def setUp(self):
        # Create users
        self.superadmin = CustomUser.objects.create_superuser(
            username='admin_mult', email='admin_mult@test.com', password='password123', usertype='superadmin'
        )
        self.customer = CustomUser.objects.create_user(
            username='cust_mult', email='cust_mult@test.com', password='password123', usertype='customer'
        )
        self.district = CustomUser.objects.create_user(
            username='dist_mult', email='dist_mult@test.com', password='password123', usertype='district'
        )
        self.mandalam = CustomUser.objects.create_user(
            username='mand_mult', email='mand_mult@test.com', password='password123', usertype='mandalam',
            assigned_district=self.district
        )
        self.marketing = CustomUser.objects.create_user(
            username='mark_mult', email='mark_mult@test.com', password='password123', usertype='marketing',
            assigned_district=self.district, assigned_mandalam=self.mandalam
        )

        # Categories
        self.category = Category.objects.create(name='MultCategory', cat_type='count', created_by=self.superadmin)
        
        # Subcategories - make two of them mandatory
        self.sub_mand_1 = SubCategory.objects.create(
            category=self.category, name='MandatorySub1', is_mandatory_target=True, created_by=self.superadmin
        )
        self.sub_mand_2 = SubCategory.objects.create(
            category=self.category, name='MandatorySub2', is_mandatory_target=True, created_by=self.superadmin
        )
        self.sub_normal = SubCategory.objects.create(
            category=self.category, name='NormalSub', is_mandatory_target=False, created_by=self.superadmin
        )

        # Customer Requirement
        self.requirement = CustomerRequirement.objects.create(
            customer=self.customer, category=self.category, title='Mult Req', status='approved'
        )
        self.requirement.customer.accessible_districts.add(self.district)

        # Requirement Items
        self.item_mand_1 = RequirementItem.objects.create(
            requirement=self.requirement, subcategory=self.sub_mand_1, count=100,
            customer_amount=Decimal('10.00'), admin_markup=Decimal('5.00')
        )
        self.item_mand_2 = RequirementItem.objects.create(
            requirement=self.requirement, subcategory=self.sub_mand_2, count=100,
            customer_amount=Decimal('10.00'), admin_markup=Decimal('5.00')
        )
        self.item_normal = RequirementItem.objects.create(
            requirement=self.requirement, subcategory=self.sub_normal, count=100,
            customer_amount=Decimal('10.00'), admin_markup=Decimal('5.00')
        )

    def test_assign_gating_logic(self):
        client = Client()
        client.login(username='dist_mult', password='password123')

        # 1. FC has not achieved target yet.
        # Try to assign normal subcategory requirement. It should fail with 400.
        post_data = {
            'mandalams': [self.mandalam.id],
            f'count_{self.mandalam.id}': 10
        }
        response = client.post(f'/requirements/item/{self.item_normal.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 400)
        self.assertIn("not achieved 20 confirmed leads", response.json()['message'])

        # 2. Try to assign first mandatory requirement (item_mand_1). This should succeed.
        response = client.post(f'/requirements/item/{self.item_mand_1.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 200)

        # 3. Try to assign second mandatory requirement (item_mand_2) while working on first one.
        # This should fail because they can only work on one mandatory subcategory at a time when target not achieved.
        response = client.post(f'/requirements/item/{self.item_mand_2.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 400)
        self.assertIn("already working on another mandatory subcategory", response.json()['message'])

        # 4. Now simulate 20 leads confirmed on MandatorySub1.
        for i in range(20):
            lead = Lead.objects.create(
                requirement=self.requirement, marketing_user=self.marketing, status='confirmed',
                phone=f'987654321{i}'
            )
            LeadItem.objects.create(lead=lead, subcategory=self.sub_mand_1, count=1)

        # Now they have achieved the target!
        # Try to assign the second mandatory requirement (item_mand_2) again. It should now succeed.
        response = client.post(f'/requirements/item/{self.item_mand_2.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 200)

        # Try to assign the normal requirement (item_normal) again. It should now succeed.
        response = client.post(f'/requirements/item/{self.item_normal.id}/assign-mandalams/', post_data)
        self.assertEqual(response.status_code, 200)

    def test_target_achievement_list(self):
        client = Client()
        client.login(username='admin_mult', password='password123')

        # Try to access as superadmin
        response = client.get('/superadmin/target-achievements/')
        self.assertEqual(response.status_code, 200)

        # Try to access as non-superadmin (should get 302 redirect)
        non_admin_client = Client()
        non_admin_client.login(username='dist_mult', password='password123')
        response = non_admin_client.get('/superadmin/target-achievements/')
        self.assertEqual(response.status_code, 302)

    def test_target_achievement_notification(self):
        # Setup manager
        manager = CustomUser.objects.create_user(
            username='mgr_mult',
            password='password123',
            usertype='manager',
            assigned_district=self.district
        )

        # Clear existing notifications
        Notification.objects.all().delete()

        # Before generating leads, there should be no notifications
        self.assertEqual(Notification.objects.count(), 0)

        # Generate 19 confirmed leads for the FC on a mandatory category
        for i in range(19):
            lead = Lead.objects.create(
                requirement=self.requirement,
                marketing_user=self.marketing,
                status='confirmed',
                phone=f'99999999{i:02d}'
            )
            # Need to trigger post_save signals, so we create item first or trigger save again
            LeadItem.objects.create(lead=lead, subcategory=self.sub_mand_1, count=1)
            # Trigger save on lead to run the post_save receiver after items are attached
            lead.save()

        # No notifications yet because we haven't reached 20
        self.assertEqual(Notification.objects.filter(verb__icontains="has achieved their 20-lead milestone").count(), 0)

        # Generate the 20th confirmed lead
        lead20 = Lead.objects.create(
            requirement=self.requirement,
            marketing_user=self.marketing,
            status='confirmed',
            phone='9999999920'
        )
        LeadItem.objects.create(lead=lead20, subcategory=self.sub_mand_1, count=1)
        lead20.save()

        # Now the notification should be triggered for superadmin, district franchise, and manager
        milestone_notifications = Notification.objects.filter(verb__icontains="has achieved their 20-lead milestone")
        
        # Recipients should include superadmin, district franchise, and manager
        recipients = [n.recipient for n in milestone_notifications]
        self.assertIn(self.superadmin, recipients)
        self.assertIn(self.district, recipients)
        self.assertIn(manager, recipients)

    def test_incentives_creation_and_wallet_credit(self):
        client = Client()
        client.login(username='admin_mult', password='password123')

        response = client.get('/superadmin/incentives/')
        self.assertEqual(response.status_code, 200)

        post_data = {
            'incentive_type': 'sale',
            'lead_from': '1',
            'lead_to': '5',
            'district_franchise_incentive': '2000.00',
            'feciliattion_center_incentive': '1000.00',
            'digital_franchise_incentive': '3000.00'
        }

        response = client.post('/superadmin/incentives/create/', post_data)
        self.assertEqual(response.status_code, 302)

        from cyborgapp.models import Incentive
        incentive = Incentive.objects.filter(incentive_type='sale').first()
        self.assertIsNotNone(incentive)
        self.assertFalse(incentive.is_active)
        self.assertEqual(incentive.digital_franchise_incentive, Decimal('3000.00'))

        # Toggle status
        response = client.post(f'/superadmin/incentives/{incentive.id}/toggle/')
        self.assertEqual(response.status_code, 302)
        incentive.refresh_from_db()
        self.assertTrue(incentive.is_active)

        # Confirm a lead under marketing user
        from cyborgapp.utils import get_or_create_wallet, distribute_product_sale_commission
        
        # Reset wallets to 0
        marketing_wallet = get_or_create_wallet(self.marketing)
        marketing_wallet.balance = Decimal('0.00')
        marketing_wallet.total_earned = Decimal('0.00')
        marketing_wallet.save()

        fc_user = self.marketing.assigned_mandalam
        if fc_user:
            fc_wallet = get_or_create_wallet(fc_user)
            fc_wallet.balance = Decimal('0.00')
            fc_wallet.total_earned = Decimal('0.00')
            fc_wallet.save()
        else:
            fc_wallet = None

        district_wallet = get_or_create_wallet(self.district)
        district_wallet.balance = Decimal('0.00')
        district_wallet.total_earned = Decimal('0.00')
        district_wallet.save()

        # Let's call the helper trigger or distribute commission which triggers it
        from cyborgapp.models import Lead
        lead = Lead.objects.create(
            name="Test Lead for Incentive",
            phone="9876543210",
            email="testlead@example.com",
            requirement=self.requirement,
            marketing_user=self.marketing,
            status='confirmed'
        )

        distribute_product_sale_commission(lead)

        marketing_wallet.refresh_from_db()
        # Since lead count is 1 (which is between 1 and 5), they should get 3000.00
        self.assertEqual(marketing_wallet.balance, Decimal('3000.00'))

        if fc_wallet:
            fc_wallet.refresh_from_db()
            self.assertEqual(fc_wallet.balance, Decimal('1000.00'))

        district_wallet.refresh_from_db()
        self.assertEqual(district_wallet.balance, Decimal('2000.00'))

    def test_part_payment_incentive_ranking(self):
        from cyborgapp.models import Lead, LeadInstallment, Incentive
        from cyborgapp.utils import get_or_create_wallet, distribute_product_sale_commission
        
        # 1. Create a 10 to 20 range incentive rule
        Incentive.objects.create(
            incentive_type='sale',
            lead_from=10,
            lead_to=20,
            digital_franchise_incentive=Decimal('1000.00'),
            is_active=True
        )

        # 2. Reset marketing user's wallet
        wallet = get_or_create_wallet(self.marketing)
        wallet.balance = Decimal('0.00')
        wallet.total_earned = Decimal('0.00')
        wallet.save()

        # 3. Simulate 19 fully paid leads (confirmed, no installments)
        for i in range(19):
            Lead.objects.create(
                requirement=self.requirement,
                marketing_user=self.marketing,
                status='confirmed',
                phone=f'9870000{i:03d}'
            )

        # 4. Create Lead A (20th lead) with part payment (has pending installments)
        lead_a = Lead.objects.create(
            requirement=self.requirement,
            marketing_user=self.marketing,
            status='confirmed',
            phone='9870000200'
        )
        # Create one paid and one pending installment for Lead A
        inst_1 = LeadInstallment.objects.create(lead=lead_a, installment_number=1, amount=Decimal('50.00'), status='paid')
        inst_2 = LeadInstallment.objects.create(lead=lead_a, installment_number=2, amount=Decimal('50.00'), status='pending')

        # Trigger commission for Lead A (e.g. on first installment)
        distribute_product_sale_commission(lead_a, installment=inst_1)

        # Verify Lead A did not trigger the incentive (wallet balance remains 0)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('0.00'))

        # 5. Create Lead B (21st lead) with full payment (no installments)
        lead_b = Lead.objects.create(
            requirement=self.requirement,
            marketing_user=self.marketing,
            status='confirmed',
            phone='9870000201'
        )
        distribute_product_sale_commission(lead_b)

        # Lead B becomes the 20th fully paid lead (leads 1-19, plus Lead B).
        # Since 20 is within range 10-20, Lead B qualifies and triggers the incentive!
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('1000.00'))

        # 6. Complete payment of the final installment of Lead A
        inst_2.status = 'paid'
        inst_2.save()
        distribute_product_sale_commission(lead_a, installment=inst_2)

        # Lead A now becomes fully paid. Since B is already fully paid, A's count is 21.
        # 21 is outside the 10-20 range, so Lead A should NOT trigger the incentive.
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('1000.00'))

    def test_incentive_edit_delete_restrictions(self):
        from cyborgapp.models import Incentive, CommissionTransaction
        
        # 1. Create an incentive rule
        inc = Incentive.objects.create(
            incentive_type='sale',
            lead_from=1,
            lead_to=5,
            digital_franchise_incentive=Decimal('500.00'),
            is_active=True
        )
        
        # Initially, has_been_used should be False
        self.assertFalse(inc.has_been_used)
        
        # 2. Simulate user getting wallet credit under this incentive
        from cyborgapp.utils import add_to_wallet
        desc = f"Incentive for lead #1 (Incentive Rule #{inc.id})"
        add_to_wallet(self.marketing, Decimal('500.00'), 'incentive', 9999, desc)
        
        # Now, has_been_used should be True
        self.assertTrue(inc.has_been_used)
        
        # 3. Try to edit via POST request
        self.client.force_login(self.superadmin)
        response = self.client.post(f'/superadmin/incentives/{inc.id}/edit/', {
            'incentive_type': 'sale',
            'lead_from': 1,
            'lead_to': 10,
            'digital_franchise_incentive': '600.00'
        })
        # Check that it redirected back
        self.assertEqual(response.status_code, 302)
        
        # Refresh and verify it was NOT edited
        inc.refresh_from_db()
        self.assertEqual(inc.lead_to, 5)
        
        # 4. Try to delete via POST request
        response = self.client.post(f'/superadmin/incentives/{inc.id}/delete/')
        self.assertEqual(response.status_code, 302)
        
        # Verify it was NOT deleted
        self.assertTrue(Incentive.objects.filter(pk=inc.id).exists())


class LeadMailPaymentLinkTest(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_superuser(
            username='admin_pay', email='admin_pay@test.com', password='password123', usertype='superadmin'
        )
        self.category = Category.objects.create(name='Test Category Pay', cat_type='fixed')
        self.subcategory = SubCategory.objects.create(category=self.category, name='Test Sub Pay')
        self.customer = CustomUser.objects.create_user(
            username='cust_pay', email='cust_pay@test.com', password='password123', usertype='customer'
        )
        self.marketing_user = CustomUser.objects.create_user(
            username='mark_pay', email='mark_pay@test.com', password='password123', usertype='marketing'
        )
        self.requirement = CustomerRequirement.objects.create(
            customer=self.customer, category=self.category, title='Pay Req', status='approved'
        )
        self.req_item = RequirementItem.objects.create(
            requirement=self.requirement, subcategory=self.subcategory,
            customer_amount=Decimal('1000.00'), admin_markup=Decimal('200.00'),
            other_expenses=Decimal('0.00'), gst=Decimal('0.00')
        )
        self.lead = Lead.objects.create(
            requirement=self.requirement, marketing_user=self.marketing_user,
            status='pending', total_amount=Decimal('1200.00')
        )
        self.lead_item = LeadItem.objects.create(lead=self.lead, subcategory=self.subcategory, count=1)

    def test_single_payment_confirmed_redirect(self):
        # Set lead status to confirmed
        self.lead.status = 'confirmed'
        self.lead.save()

        # Access pay-from-mail view for the confirmed lead without being logged in
        response = self.client.get(f'/leads/{self.lead.id}/pay-from-mail/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'cyborgapp/leads/payment_status.html')
        self.assertEqual(response.context['status'], 'already_confirmed')

    def test_installment_payment_preventions(self):
        self.lead.status = 'confirmed'
        self.lead.payment_mode = 'part'
        self.lead.save()

        inst1 = LeadInstallment.objects.create(
            lead=self.lead, installment_number=1, amount=Decimal('600.00'), status='pending'
        )
        inst2 = LeadInstallment.objects.create(
            lead=self.lead, installment_number=2, amount=Decimal('600.00'), status='pending'
        )

        # 1. Attempt to pay inst2 from mail before paying inst1
        response = self.client.get(f'/installments/{inst2.id}/pay-from-mail/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'cyborgapp/leads/payment_status.html')
        self.assertEqual(response.context['status'], 'previous_pending')

        # 2. Set inst1 status to paid
        inst1.status = 'paid'
        inst1.save()

        # Attempt to pay inst1 again from mail
        response = self.client.get(f'/installments/{inst1.id}/pay-from-mail/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'cyborgapp/leads/payment_status.html')
        self.assertEqual(response.context['status'], 'installment_already_paid')

        # 3. Attempt to pay inst2 from mail now that inst1 is paid (should generate razorpay payment link)
        from unittest.mock import patch
        with patch('razorpay.Client') as mock_razorpay:
            instance = mock_razorpay.return_value
            instance.payment_link.create.return_value = {
                'short_url': 'https://rzp.io/i/mocked_link'
            }
            response = self.client.get(f'/installments/{inst2.id}/pay-from-mail/')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, 'https://rzp.io/i/mocked_link')


class ManualWithdrawalApprovalTestCase(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username='superadmin_payout@cyborg.com',
            email='superadmin_payout@cyborg.com',
            name='Super Admin Payout',
            usertype='superadmin',
            password='password123'
        )
        self.marketing_user = CustomUser.objects.create_user(
            username='m_payout@cyborg.com',
            email='m_payout@cyborg.com',
            name='Marketing User Payout',
            usertype='marketing',
            password='password123'
        )
        from cyborgapp.utils import get_or_create_wallet
        self.wallet = get_or_create_wallet(self.marketing_user)
        self.wallet.balance = Decimal('5000.00')
        self.wallet.total_earned = Decimal('5000.00')
        self.wallet.save()

        self.wr = WithdrawalRequest.objects.create(
            user=self.marketing_user,
            amount=Decimal('1000.00'),
            request_type='wallet',
            account_number='987654321012',
            ifsc_code='HDFC0000053',
            account_holder='Marketing User Payout',
            phone_linked='9876543210',
            status='pending'
        )

    def test_superadmin_approval_deducts_wallet_balance(self):
        import json
        client = Client()
        client.login(username='superadmin_payout@cyborg.com', password='password123')
        
        response = client.post(f'/wallet/requests/{self.wr.id}/update/', json.dumps({
            'status': 'approved',
            'remarks': 'Manually approved by superadmin'
        }), content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        
        self.wr.refresh_from_db()
        self.assertEqual(self.wr.status, 'approved')
        self.assertEqual(self.wr.remarks, 'Manually approved by superadmin')
        
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('4000.00'))
        self.assertEqual(self.wallet.withdrawn_amount, Decimal('1000.00'))

    def test_superadmin_rejection_leaves_wallet_balance_intact(self):
        import json
        client = Client()
        client.login(username='superadmin_payout@cyborg.com', password='password123')
        
        response = client.post(f'/wallet/requests/{self.wr.id}/update/', json.dumps({
            'status': 'rejected',
            'remarks': 'Rejected by superadmin'
        }), content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        
        self.wr.refresh_from_db()
        self.assertEqual(self.wr.status, 'rejected')
        
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('5000.00'))
        self.assertEqual(self.wallet.withdrawn_amount, Decimal('0.00'))

    def test_insufficient_balance_prevents_approval(self):
        import json
        self.wallet.balance = Decimal('500.00')
        self.wallet.save()
        
        client = Client()
        client.login(username='superadmin_payout@cyborg.com', password='password123')
        
        response = client.post(f'/wallet/requests/{self.wr.id}/update/', json.dumps({
            'status': 'approved',
            'remarks': 'Approve'
        }), content_type='application/json')
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('insufficient balance', response.json()['message'].lower())
        
        self.wr.refresh_from_db()
        self.assertEqual(self.wr.status, 'pending')


class AssociateCompanyLeadCompletionTestCase(TestCase):
    def setUp(self):
        from .models import Category, SubCategory
        self.customer = CustomUser.objects.create_user(
            username='assoc_customer@cyborg.com',
            email='assoc_customer@cyborg.com',
            password='password123',
            usertype='customer',
            name='Test Associate Company'
        )
        self.marketing_user = CustomUser.objects.create_user(
            username='mkt_user@cyborg.com',
            email='mkt_user@cyborg.com',
            password='password123',
            usertype='marketing'
        )
        self.cat = Category.objects.create(name='Test Category', cat_type='service')
        self.subcat = SubCategory.objects.create(category=self.cat, name='Test Subcat')
        
        self.req = CustomerRequirement.objects.create(
            customer=self.customer,
            category=self.cat,
            title='Test Requirement',
            status='approved'
        )
        self.lead = Lead.objects.create(
            requirement=self.req,
            marketing_user=self.marketing_user,
            name='Confirmed Lead',
            phone='1234567890',
            status='confirmed',
            total_amount=1000
        )

    def test_associate_company_mark_lead_completed_and_block_further_updates(self):
        import json
        client = Client()
        client.login(username='assoc_customer@cyborg.com', password='password123')
        
        # 1. Fetch updates before completion -> can_add=True, is_completed=False
        res = client.get(f'/leads/{self.lead.id}/associate-updates/get/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['can_add'])
        self.assertFalse(res.json()['is_completed'])
        
        # 2. Submit update with mark_completed=True
        res = client.post(
            f'/leads/{self.lead.id}/associate-updates/add/',
            json.dumps({'update_text': 'Work finished', 'mark_completed': True}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'success')
        self.assertTrue(res.json()['is_completed'])
        
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'completed')
        
        # 3. Fetch updates after completion -> can_add=False, is_completed=True
        res = client.get(f'/leads/{self.lead.id}/associate-updates/get/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['can_add'])
        self.assertTrue(res.json()['is_completed'])
        
        # 4. Attempting to add another update should fail with status 400
        res = client.post(
            f'/leads/{self.lead.id}/associate-updates/add/',
            json.dumps({'update_text': 'Trying another update'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('already completed', res.json()['message'])

    def test_associate_company_cannot_complete_non_confirmed_lead(self):
        import json
        pending_lead = Lead.objects.create(
            requirement=self.req,
            marketing_user=self.marketing_user,
            name='Pending Lead',
            phone='1112223334',
            status='pending',
            total_amount=500
        )
        client = Client()
        client.login(username='assoc_customer@cyborg.com', password='password123')

        # Check get updates API -> can_mark_completed should be False
        res = client.get(f'/leads/{pending_lead.id}/associate-updates/get/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['can_mark_completed'])

        # Attempt to mark completed -> should return HTTP 400 error
        res = client.post(
            f'/leads/{pending_lead.id}/associate-updates/add/',
            json.dumps({'update_text': 'Try mark complete', 'mark_completed': True}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)

        pending_lead.refresh_from_db()
        self.assertEqual(pending_lead.status, 'pending')

    def test_associate_company_cannot_complete_lead_with_pending_installments(self):
        import json
        from .models import LeadInstallment
        part_lead = Lead.objects.create(
            requirement=self.req,
            marketing_user=self.marketing_user,
            name='Part Payment Lead',
            phone='1112223335',
            status='confirmed',
            payment_mode='part',
            total_amount=1000
        )
        # 1st installment paid, 2nd installment pending
        LeadInstallment.objects.create(lead=part_lead, installment_number=1, amount=500, status='paid')
        LeadInstallment.objects.create(lead=part_lead, installment_number=2, amount=500, status='pending')

        client = Client()
        client.login(username='assoc_customer@cyborg.com', password='password123')

        # Check get updates API -> can_mark_completed should be False, lead_status should be Payment Pending
        res = client.get(f'/leads/{part_lead.id}/associate-updates/get/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()['can_mark_completed'])
        self.assertIn('Payment Pending (so cannot change to completed)', res.json()['lead_status'])

        # Attempt to mark completed -> should return HTTP 400 error
        res = client.post(
            f'/leads/{part_lead.id}/associate-updates/add/',
            json.dumps({'update_text': 'Try mark complete with pending installment', 'mark_completed': True}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('all installments cleared', res.json()['message'])

        part_lead.refresh_from_db()
        self.assertEqual(part_lead.status, 'confirmed')

    def test_part_payment_proportional_gst_and_expense(self):
        from .models import RequirementItem, LeadItem, LeadInstallment
        subcat = SubCategory.objects.create(category=self.cat, name='Subcat Proportional GST')
        req_item = RequirementItem.objects.create(
            requirement=self.req,
            subcategory=subcat,
            customer_amount=Decimal('1000.00'),
            admin_markup=Decimal('200.00'),
            other_expenses=Decimal('100.00'),
            gst=Decimal('18.00')
        )
        # Total base = 1300, total GST = 234, total amount = 1534
        part_lead = Lead.objects.create(
            requirement=self.req,
            marketing_user=self.marketing_user,
            name='Part Lead Proportional',
            phone='9990001112',
            status='confirmed',
            payment_mode='part',
            total_amount=Decimal('1534.00')
        )
        LeadItem.objects.create(lead=part_lead, subcategory=subcat)

        inst1 = LeadInstallment.objects.create(lead=part_lead, installment_number=1, amount=Decimal('767.00'), status='paid')
        inst2 = LeadInstallment.objects.create(lead=part_lead, installment_number=2, amount=Decimal('767.00'), status='pending')

        # 50% paid -> GST should be 117.00 (50% of 234), Expense should be 50.00 (50% of 100)
        self.assertEqual(part_lead.get_gst_amount, Decimal('117.00'))
        self.assertEqual(part_lead.get_expense_amount, Decimal('50.00'))

        # Pay 2nd installment (100% paid)
        inst2.status = 'paid'
        inst2.save()

        # 100% paid -> GST should be 234.00, Expense should be 100.00
        self.assertEqual(part_lead.get_gst_amount, Decimal('234.00'))
        self.assertEqual(part_lead.get_expense_amount, Decimal('100.00'))

    def test_associate_company_notifications_visibility_and_recipients(self):
        import json
        superadmin = CustomUser.objects.create_user(
            username='sa_notif@cyborg.com',
            email='sa_notif@cyborg.com',
            password='password123',
            usertype='superadmin'
        )
        district = CustomUser.objects.create_user(
            username='district_notif@cyborg.com',
            email='district_notif@cyborg.com',
            password='password123',
            usertype='district'
        )
        mandalam = CustomUser.objects.create_user(
            username='mandalam_notif@cyborg.com',
            email='mandalam_notif@cyborg.com',
            password='password123',
            usertype='mandalam'
        )
        manager = CustomUser.objects.create_user(
            username='manager_notif@cyborg.com',
            email='manager_notif@cyborg.com',
            password='password123',
            usertype='manager',
            assigned_district=district
        )
        self.marketing_user.assigned_district = district
        self.marketing_user.assigned_mandalam = mandalam
        self.marketing_user.save()

        # Submit status completion as associate company
        client = Client()
        client.login(username='assoc_customer@cyborg.com', password='password123')
        client.post(
            f'/leads/{self.lead.id}/associate-updates/add/',
            json.dumps({'update_text': 'Completed work', 'mark_completed': True}),
            content_type='application/json'
        )

        # Check Superadmin notifications -> actor should be company name
        sa_client = Client()
        sa_client.login(username='sa_notif@cyborg.com', password='password123')
        res = sa_client.get('/api/notifications/')
        self.assertEqual(res.status_code, 200)
        sa_notes = res.json()['notifications']
        self.assertTrue(len(sa_notes) > 0)
        self.assertEqual(sa_notes[0]['actor'], 'Test Associate Company')

        # Check District Franchise notifications -> actor should be "Associate Company"
        df_client = Client()
        df_client.login(username='district_notif@cyborg.com', password='password123')
        res = df_client.get('/api/notifications/')
        self.assertEqual(res.status_code, 200)
        df_notes = res.json()['notifications']
        self.assertTrue(len(df_notes) > 0)
        self.assertEqual(df_notes[0]['actor'], 'Associate Company')

    def test_completed_lead_gst_and_confirmed_nav_behaviour(self):
        from .models import RequirementItem
        superadmin = CustomUser.objects.create_user(
            username='sa_gst_test@cyborg.com',
            email='sa_gst_test@cyborg.com',
            password='password123',
            usertype='superadmin'
        )
        subcat = SubCategory.objects.create(category=self.cat, name='Subcat GST')
        req_item = RequirementItem.objects.create(
            requirement=self.req,
            subcategory=subcat,
            customer_amount=Decimal('1000.00'),
            admin_markup=Decimal('200.00'),
            other_expenses=Decimal('100.00'),
            gst=Decimal('18.00')
        )
        lead_comp = Lead.objects.create(
            requirement=self.req,
            marketing_user=self.marketing_user,
            name='Completed Lead GST',
            phone='9998887776',
            status='completed',
            total_amount=Decimal('1534.00'),
            payment_mode='single'
        )
        from .models import LeadItem
        LeadItem.objects.create(lead=lead_comp, subcategory=subcat)

        client = Client()
        client.login(username='sa_gst_test@cyborg.com', password='password123')

        # 1. Verify completed lead IS included in confirmed leads list
        res_conf_nav = client.get('/leads/confirmed/')
        self.assertEqual(res_conf_nav.status_code, 200)
        self.assertIn(lead_comp, res_conf_nav.context['leads'])

        # 2. Verify completed lead is NOT in main leads list
        res_main_nav = client.get('/leads/')
        self.assertEqual(res_main_nav.status_code, 200)
        self.assertNotIn(lead_comp, res_main_nav.context['leads'])

        # 3. Verify completed lead IS included in GST & Expense calculations
        res_gst = client.get('/superadmin/gst/')
        self.assertEqual(res_gst.status_code, 200)
        self.assertTrue(res_gst.context['total_earned'] >= Decimal('234.00')) # 1300 * 18%

        res_exp = client.get('/superadmin/expenses/')
        self.assertEqual(res_exp.status_code, 200)
        self.assertTrue(res_exp.context['total_earned'] >= Decimal('100.00'))

    def test_associate_company_wallet_split_and_completion_transfer(self):
        from .models import RequirementItem, LeadItem, Wallet, AssociateWalletLog
        from .utils import distribute_product_sale_commission

        assoc_user = CustomUser.objects.create_user(
            username='assoc_split_test@cyborg.com',
            email='assoc_split_test@cyborg.com',
            password='password123',
            usertype='customer',
            name='Test Associate Split Co'
        )

        cat = Category.objects.create(name='Cat Split Test')
        req = CustomerRequirement.objects.create(
            customer=assoc_user,
            title='Split Test Project',
            category=cat
        )
        subcat = SubCategory.objects.create(category=cat, name='Subcat Split')
        req_item = RequirementItem.objects.create(
            requirement=req,
            subcategory=subcat,
            customer_amount=Decimal('2000.00'),
            admin_markup=Decimal('0.00'),
            other_expenses=Decimal('0.00'),
            gst=Decimal('0.00')
        )
        lead = Lead.objects.create(
            requirement=req,
            marketing_user=self.marketing_user,
            name='Split Lead',
            phone='1234567890',
            status='confirmed',
            total_amount=Decimal('2000.00'),
            payment_mode='single'
        )
        LeadItem.objects.create(lead=lead, subcategory=subcat)

        # Distribute commission
        distribute_product_sale_commission(lead)

        wallet = Wallet.objects.get(user=assoc_user)
        # Check total_earned is 2000
        self.assertEqual(wallet.total_earned, Decimal('2000.00'))
        # Check 50% added to current balance (1000)
        self.assertEqual(wallet.balance, Decimal('1000.00'))
        # Check 50% added to total withdrawal earned (1000)
        self.assertEqual(wallet.total_withdrawal_earned, Decimal('1000.00'))
        # Check 50% added to pending balance (1000)
        self.assertEqual(wallet.pending_balance, Decimal('1000.00'))

        # Check AssociateWalletLog records created
        wd_log = AssociateWalletLog.objects.get(user=assoc_user, lead=lead, log_type='withdrawal')
        self.assertEqual(wd_log.amount, Decimal('1000.00'))
        self.assertIn('reflected to withdrawal balance', wd_log.description)

        pending_log = AssociateWalletLog.objects.get(user=assoc_user, lead=lead, log_type='pending')
        self.assertEqual(pending_log.amount, Decimal('1000.00'))
        self.assertIn('reflected to pending balance', pending_log.description)

        # Now mark lead completed via lead_add_associate_update
        import json
        client = Client()
        client.login(username='assoc_split_test@cyborg.com', password='password123')
        res = client.post(
            f'/leads/{lead.id}/associate-updates/add/',
            json.dumps({'update_text': 'Finished work', 'mark_completed': True}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)

        wallet.refresh_from_db()
        # Pending balance should be 0
        self.assertEqual(wallet.pending_balance, Decimal('0.00'))
        # Current balance should be 2000
        self.assertEqual(wallet.balance, Decimal('2000.00'))
        # Total withdrawal earned should be 2000
        self.assertEqual(wallet.total_withdrawal_earned, Decimal('2000.00'))
        # Total earned remains 2000
        self.assertEqual(wallet.total_earned, Decimal('2000.00'))

        # Verify transfer log created
        transfer_pending_log = AssociateWalletLog.objects.filter(
            user=assoc_user, lead=lead, log_type='pending', description__contains='transferred to withdrawal balance'
        ).first()
        self.assertIsNotNone(transfer_pending_log)
        self.assertEqual(transfer_pending_log.amount, Decimal('1000.00'))

        transfer_wd_log = AssociateWalletLog.objects.filter(
            user=assoc_user, lead=lead, log_type='withdrawal', description__contains='credited after completion'
        ).first()
        self.assertIsNotNone(transfer_wd_log)
        self.assertEqual(transfer_wd_log.amount, Decimal('1000.00'))

    def test_dynamic_associate_company_withdrawal_percentage(self):
        from .models import RequirementItem, LeadItem, Wallet, AssociateWalletLog
        from .utils import distribute_product_sale_commission

        # Create Associate Company with custom initial_withdrawal_percentage = 60.00
        assoc_user = CustomUser.objects.create_user(
            username='assoc_dynamic_test@cyborg.com',
            email='assoc_dynamic_test@cyborg.com',
            password='password123',
            usertype='customer',
            name='Test Dynamic Split Co',
            initial_withdrawal_percentage=Decimal('60.00')
        )

        cat = Category.objects.create(name='Cat Dynamic Test')
        req = CustomerRequirement.objects.create(
            customer=assoc_user,
            title='Dynamic Split Test Project',
            category=cat
        )
        subcat = SubCategory.objects.create(category=cat, name='Subcat Dynamic')
        RequirementItem.objects.create(
            requirement=req,
            subcategory=subcat,
            customer_amount=Decimal('2000.00'),
            admin_markup=Decimal('0.00'),
            other_expenses=Decimal('0.00'),
            gst=Decimal('0.00')
        )
        lead = Lead.objects.create(
            requirement=req,
            marketing_user=self.marketing_user,
            name='Dynamic Split Lead',
            phone='1234567890',
            status='confirmed',
            total_amount=Decimal('2000.00'),
            payment_mode='single'
        )
        LeadItem.objects.create(lead=lead, subcategory=subcat)

        # Distribute commission
        distribute_product_sale_commission(lead)

        wallet = Wallet.objects.get(user=assoc_user)
        # Check total_earned is 2000
        self.assertEqual(wallet.total_earned, Decimal('2000.00'))
        # 60% of 2000 = 1200 added to current balance
        self.assertEqual(wallet.balance, Decimal('1200.00'))
        # 60% of 2000 = 1200 added to total withdrawal earned
        self.assertEqual(wallet.total_withdrawal_earned, Decimal('1200.00'))
        # 40% of 2000 = 800 added to pending balance
        self.assertEqual(wallet.pending_balance, Decimal('800.00'))

        # Check AssociateWalletLog records created
        wd_log = AssociateWalletLog.objects.get(user=assoc_user, lead=lead, log_type='withdrawal')
        self.assertEqual(wd_log.amount, Decimal('1200.00'))

        pending_log = AssociateWalletLog.objects.get(user=assoc_user, lead=lead, log_type='pending')
        self.assertEqual(pending_log.amount, Decimal('800.00'))

        # Complete lead and verify remaining 800 (40%) moves to withdrawal balance
        import json
        client = Client()
        client.login(username='assoc_dynamic_test@cyborg.com', password='password123')
        res = client.post(
            f'/leads/{lead.id}/associate-updates/add/',
            json.dumps({'update_text': 'Completed work', 'mark_completed': True}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)

        wallet.refresh_from_db()
        self.assertEqual(wallet.pending_balance, Decimal('0.00'))
        self.assertEqual(wallet.balance, Decimal('2000.00'))
        self.assertEqual(wallet.total_withdrawal_earned, Decimal('2000.00'))


class DistrictFeedbackTestCase(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username='sa_fb@cyborg.com', email='sa_fb@cyborg.com', password='password123', usertype='superadmin'
        )
        self.district = CustomUser.objects.create_user(
            username='dist_fb@cyborg.com', email='dist_fb@cyborg.com', password='password123', usertype='district'
        )
        self.manager = CustomUser.objects.create_user(
            username='mgr_fb@cyborg.com', email='mgr_fb@cyborg.com', password='password123', usertype='manager', assigned_district=self.district
        )
        self.marketer = CustomUser.objects.create_user(
            username='mkt_fb@cyborg.com', email='mkt_fb@cyborg.com', password='password123', usertype='marketing', assigned_district=self.district
        )
        self.customer = CustomUser.objects.create_user(
            username='cust_fb@cyborg.com', email='cust_fb@cyborg.com', password='password123', usertype='customer'
        )

        cat = Category.objects.create(name='Cat FB Test')
        self.req = CustomerRequirement.objects.create(
            customer=self.customer, title='FB Test Project', category=cat, status='approved'
        )
        self.lead = Lead.objects.create(
            requirement=self.req,
            marketing_user=self.marketer,
            name='Test Lead Feedback',
            phone='9998887776',
            current_level='marketing',
            status='pending'
        )

    def test_district_feedback_workflow_and_permissions(self):
        from .models import DistrictFeedback
        import json
        client = Client()

        # 1. Try to add feedback when lead is at marketing level (should fail)
        client.login(username='dist_fb@cyborg.com', password='password123')
        res = client.post(
            f'/leads/{self.lead.id}/district-feedback/add/',
            json.dumps({'feedback_text': 'Premature feedback'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn('District Franchise level', res.json()['message'])

        # 2. Advance lead to district level
        self.lead.current_level = 'district'
        self.lead.save()

        # 3. Manager attempts to add feedback (MUST be forbidden)
        client.login(username='mgr_fb@cyborg.com', password='password123')
        res_mgr = client.post(
            f'/leads/{self.lead.id}/district-feedback/add/',
            json.dumps({'feedback_text': 'Manager trying to give feedback'}),
            content_type='application/json'
        )
        self.assertEqual(res_mgr.status_code, 403)
        self.assertIn('Managers are not allowed', res_mgr.json()['message'])

        # 4. District Franchise attempts to add feedback without date/time (MUST fail)
        client.login(username='dist_fb@cyborg.com', password='password123')
        res_nodate = client.post(
            f'/leads/{self.lead.id}/district-feedback/add/',
            json.dumps({'feedback_text': 'District franchise feedback note'}),
            content_type='application/json'
        )
        self.assertEqual(res_nodate.status_code, 400)
        self.assertIn('date and feedback time are required', res_nodate.json()['message'])

        # 5. District Franchise adds feedback with date and time (MUST succeed)
        res_dist = client.post(
            f'/leads/{self.lead.id}/district-feedback/add/',
            json.dumps({
                'feedback_text': 'District franchise feedback note',
                'custom_date': '2026-09-10',
                'custom_time': '10:30'
            }),
            content_type='application/json'
        )
        self.assertEqual(res_dist.status_code, 200)
        self.assertEqual(res_dist.json()['status'], 'success')
        self.assertEqual(DistrictFeedback.objects.count(), 1)

        # 5. Superadmin fetches and views the feedback added by district franchise
        client.login(username='sa_fb@cyborg.com', password='password123')
        res_sa_get = client.get(f'/leads/{self.lead.id}/district-feedback/get/')
        self.assertEqual(res_sa_get.status_code, 200)
        sa_data = res_sa_get.json()
        self.assertEqual(sa_data['status'], 'success')
        self.assertEqual(len(sa_data['feedbacks']), 1)
        self.assertEqual(sa_data['feedbacks'][0]['feedback_text'], 'District franchise feedback note')
        self.assertFalse(sa_data['can_add'])

        # 6. District Franchise fetches feedback
        client.login(username='dist_fb@cyborg.com', password='password123')
        res_dist_get = client.get(f'/leads/{self.lead.id}/district-feedback/get/')
        self.assertEqual(res_dist_get.status_code, 200)
        dist_data = res_dist_get.json()
        self.assertTrue(dist_data['can_add'])

        # 7. Manager attempts to fetch feedback (MUST be forbidden)
        client.login(username='mgr_fb@cyborg.com', password='password123')
        res_mgr_get = client.get(f'/leads/{self.lead.id}/district-feedback/get/')
        self.assertEqual(res_mgr_get.status_code, 403)

    def test_export_feedback_leads_csv(self):
        from .models import DistrictFeedback
        client = Client()

        # Non-superadmin access should be denied
        client.login(username='dist_fb@cyborg.com', password='password123')
        res_denied = client.get('/leads/export-feedback-csv/?check_empty=true')
        self.assertEqual(res_denied.status_code, 403)

        # Superadmin access
        client.login(username='sa_fb@cyborg.com', password='password123')

        # 1. Check empty before feedback added
        res_empty = client.get('/leads/export-feedback-csv/?check_empty=true')
        self.assertEqual(res_empty.status_code, 200)
        self.assertTrue(res_empty.json()['empty'])

        # 2. Add multiple DistrictFeedbacks for the same lead
        DistrictFeedback.objects.create(
            lead=self.lead,
            district_user=self.district,
            feedback_text='Export test feedback note 1'
        )
        DistrictFeedback.objects.create(
            lead=self.lead,
            district_user=self.district,
            feedback_text='Export test feedback note 2'
        )

        # 3. Check empty after feedback added -> False
        res_not_empty = client.get('/leads/export-feedback-csv/?check_empty=true')
        self.assertEqual(res_not_empty.status_code, 200)
        self.assertFalse(res_not_empty.json()['empty'])

        # 4. Download CSV export
        res_download = client.get('/leads/export-feedback-csv/')
        self.assertEqual(res_download.status_code, 200)
        self.assertEqual(res_download['Content-Type'], 'text/csv')
        content = b''.join(res_download.streaming_content).decode('utf-8')
        self.assertIn('Feedback Text', content)
        self.assertIn('1. Export test feedback note 1', content)
        self.assertIn('2. Export test feedback note 2', content)
        self.assertIn(self.lead.name, content)
        self.assertNotIn('Total Amount (Rs)', content)
        self.assertNotIn('Payment Mode', content)

        # Ensure only 2 lines in CSV (1 header + 1 row for lead)
        csv_lines = [line for line in content.strip().splitlines() if line]
        # In multiline CSV cells, splitlines splits by newline, so the lead name appears exactly once in header + data
        lead_name_occurrences = content.count(self.lead.name)
        self.assertEqual(lead_name_occurrences, 1)

        # 5. Confirmed lead feedback should be excluded
        self.lead.status = 'confirmed'
        self.lead.save()
        res_confirmed_empty = client.get('/leads/export-feedback-csv/?check_empty=true')
        self.assertTrue(res_confirmed_empty.json()['empty'])


class ManagerAccessControlTestCase(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username='sa_mac@cyborg.com', email='sa_mac@cyborg.com', password='password123', usertype='superadmin'
        )
        self.district = CustomUser.objects.create_user(
            username='dist_mac@cyborg.com', email='dist_mac@cyborg.com', password='password123', usertype='district'
        )
        self.manager = CustomUser.objects.create_user(
            username='mgr_mac@cyborg.com', email='mgr_mac@cyborg.com', password='password123', usertype='manager', assigned_district=self.district
        )
        self.mandalam = CustomUser.objects.create_user(
            username='fc_mac@cyborg.com', email='fc_mac@cyborg.com', password='password123', usertype='mandalam', assigned_district=self.district
        )
        self.marketer = CustomUser.objects.create_user(
            username='df_mac@cyborg.com', email='df_mac@cyborg.com', password='password123', usertype='marketing', assigned_district=self.district, assigned_mandalam=self.mandalam
        )

        cat = Category.objects.create(name='Mac Cat Test')
        self.req = CustomerRequirement.objects.create(
            customer=self.superadmin, title='Mac Test Requirement', category=cat, status='approved'
        )
        self.lead = Lead.objects.create(
            requirement=self.req,
            marketing_user=self.marketer,
            name='Mac Active Lead',
            phone='1234567890',
            current_level='manager',
            status='pending'
        )

    def test_district_can_create_and_edit_manager_permissions(self):
        client = Client()
        client.login(username='dist_mac@cyborg.com', password='password123')

        # 1. District creates a Manager with custom permissions
        res = client.post('/superadmin/users/create/', {
            'name': 'District Created Manager',
            'email': 'dist_mgr@cyborg.com',
            'usertype': 'manager',
            'password': 'password123',
            'mgr_can_access_requirements': 'off',
            'mgr_df_can_view': 'on',
            'mgr_df_can_create': 'off',
            'mgr_df_can_edit': 'off',
            'mgr_df_can_delete': 'off',
            'mgr_fc_can_view': 'on',
            'mgr_fc_can_create': 'on',
            'mgr_fc_can_edit': 'off',
            'mgr_fc_can_delete': 'off',
            'mgr_leads_access': 'view',
            'mgr_confirmed_leads_access': 'none',
        })
        self.assertEqual(res.status_code, 302)
        new_mgr = CustomUser.objects.get(email='dist_mgr@cyborg.com')
        self.assertEqual(new_mgr.assigned_district, self.district)
        
        perm = new_mgr.get_manager_permissions()
        self.assertFalse(perm.can_access_requirements)
        self.assertTrue(perm.df_can_view)
        self.assertFalse(perm.df_can_create)
        self.assertTrue(perm.fc_can_create)
        self.assertEqual(perm.leads_access, 'view')
        self.assertEqual(perm.confirmed_leads_access, 'none')

        # 2. District edits Manager permissions
        res_edit = client.post(f'/superadmin/users/{new_mgr.id}/edit/', {
            'name': 'District Created Manager Edited',
            'email': 'dist_mgr@cyborg.com',
            'usertype': 'manager',
            'mgr_can_access_requirements': 'on',
            'mgr_df_can_view': 'on',
            'mgr_df_can_create': 'on',
            'mgr_df_can_edit': 'on',
            'mgr_df_can_delete': 'on',
            'mgr_fc_can_view': 'on',
            'mgr_fc_can_create': 'on',
            'mgr_fc_can_edit': 'on',
            'mgr_fc_can_delete': 'on',
            'mgr_leads_access': 'action',
            'mgr_confirmed_leads_access': 'on',
        })
        self.assertEqual(res_edit.status_code, 302)
        perm.refresh_from_db()
        self.assertTrue(perm.can_access_requirements)
        self.assertEqual(perm.leads_access, 'action')
        self.assertEqual(perm.confirmed_leads_access, 'view')

    def test_manager_access_restrictions(self):
        client = Client()
        # Set manager permissions: no requirements access, view-only leads, no confirmed leads
        perm = self.manager.get_manager_permissions()
        perm.can_access_requirements = False
        perm.leads_access = 'view'
        perm.confirmed_leads_access = 'none'
        perm.df_can_create = False
        perm.save()

        client.login(username='mgr_mac@cyborg.com', password='password123')

        # 1. Accessing requirements should fail/redirect
        res_req = client.get('/requirements/')
        self.assertEqual(res_req.status_code, 302)

        # 2. Accessing confirmed leads should fail/redirect
        res_conf = client.get('/leads/confirmed/')
        self.assertEqual(res_conf.status_code, 302)

        # 3. Accessing leads list should succeed
        res_leads = client.get('/leads/')
        self.assertEqual(res_leads.status_code, 200)

        # 4. Attempting status update on lead with view-only leads access should fail
        import json
        res_update = client.post(
            f'/leads/{self.lead.id}/update/',
            json.dumps({'status': 'confirmed', 'notes': 'Trying to update'}),
            content_type='application/json'
        )
        self.assertEqual(res_update.status_code, 403)

        # 5. Attempting to create DF user without df_can_create should fail
        res_create_df = client.post('/superadmin/users/create/', {
            'name': 'Forbidden DF',
            'email': 'forb_df@cyborg.com',
            'usertype': 'marketing',
            'password': 'password123'
        })
        self.assertEqual(res_create_df.status_code, 302)
        self.assertFalse(CustomUser.objects.filter(email='forb_df@cyborg.com').exists())

    def test_manager_lead_notification_filtering(self):
        from .views import create_lead_notification
        from .models import Notification

        # Restrict manager from confirmed leads notifications
        perm = self.manager.get_manager_permissions()
        perm.leads_access = 'action'
        perm.confirmed_leads_access = 'none'
        perm.save()

        # Send pending lead notification (manager SHOULD receive)
        self.lead.status = 'pending'
        self.lead.save()
        create_lead_notification(self.superadmin, self.lead, "updated lead details")
        self.assertTrue(Notification.objects.filter(recipient=self.manager).exists())

        Notification.objects.all().delete()

        # Send confirmed lead notification (manager SHOULD NOT receive)
        self.lead.status = 'confirmed'
        self.lead.save()
        create_lead_notification(self.superadmin, self.lead, "confirmed the lead")
        self.assertFalse(Notification.objects.filter(recipient=self.manager).exists())

    def test_manager_full_action_lead_control_and_edit(self):
        client = Client()
        # Set manager permissions to action on leads
        perm = self.manager.get_manager_permissions()
        perm.leads_access = 'action'
        perm.save()

        # Set lead current level to district (which matches manager's assigned district)
        self.lead.current_level = 'district'
        self.lead.status = 'pending'
        self.lead.save()

        client.login(username='mgr_mac@cyborg.com', password='password123')

        # 1. Fetch leads list and check that is_controlled is 1 for manager
        res = client.get('/leads/')
        self.assertEqual(res.status_code, 200)
        leads_in_ctx = res.context['leads']
        target_lead = [l for l in leads_in_ctx if l.id == self.lead.id][0]
        self.assertEqual(target_lead.is_controlled, 1)

        # 2. Edit lead as Manager
        edit_res = client.post(f'/leads/{self.lead.id}/edit/', {
            'name': 'Updated Lead Name by Manager',
            'phone': self.lead.phone,
            'email': self.lead.email or '',
            'address': self.lead.address or '',
            'remarks': 'Updated remarks'
        })
        self.assertEqual(edit_res.status_code, 302)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.name, 'Updated Lead Name by Manager')

    def test_pending_lead_email_subcategories_validation(self):
        sub = SubCategory.objects.create(category=self.req.category, name='SubValTest')
        req_item = RequirementItem.objects.create(requirement=self.req, subcategory=sub, count=100)
        RequirementAssignment.objects.create(requirement_item=req_item, facilitation_center=self.mandalam, assigned_count=50)

        # Create second Digital Franchise user
        df2 = CustomUser.objects.create_user(
            username='df2_user', email='df2@test.com', password='password123', usertype='marketing',
            assigned_district=self.district, assigned_mandalam=self.mandalam
        )

        client1 = Client()
        client1.login(username=self.marketer.username, password='password123')

        # 1. DF1 creates a pending lead for subcategory 1 with email dup@test.com
        res1 = client1.post(f'/requirements/{self.req.id}/lead/create/', {
            'name': 'Lead 1',
            'phone': '1111111111',
            'email': 'dup@test.com',
            'selected_items': [sub.id],
            f'count_{sub.id}': 1
        })
        self.assertEqual(res1.status_code, 302)
        lead1 = Lead.objects.get(email='dup@test.com')
        self.assertEqual(lead1.status, 'pending')

        # 1b. DF1 attempts to create ANOTHER pending lead for the SAME subcategory with email dup@test.com -> ALLOWED
        res1b = client1.post(f'/requirements/{self.req.id}/lead/create/', {
            'name': 'Lead 1b by DF1',
            'phone': '1111111112',
            'email': 'dup@test.com',
            'selected_items': [sub.id],
            f'count_{sub.id}': 1
        })
        self.assertEqual(res1b.status_code, 302)
        self.assertEqual(Lead.objects.filter(email='dup@test.com').count(), 2)

        # 2. DF2 attempts to create a lead for the SAME requirement and SAME subcategory with email dup@test.com -> BLOCKED
        client2 = Client()
        client2.login(username='df2_user', password='password123')
        res2 = client2.post(f'/requirements/{self.req.id}/lead/create/', {
            'name': 'Lead 2 by DF2',
            'phone': '2222222222',
            'email': 'dup@test.com',
            'selected_items': [sub.id],
            f'count_{sub.id}': 1
        })
        self.assertEqual(res2.status_code, 302)
        # Should be blocked, so lead count for dup@test.com remains 2
        self.assertEqual(Lead.objects.filter(email='dup@test.com').count(), 2)

        # 3. Change status of all DF1 leads to 'confirmed'
        Lead.objects.filter(email='dup@test.com').update(status='confirmed')

        # 4. DF2 attempts again to create lead with dup@test.com for same subcategory -> ALLOWED
        res3 = client2.post(f'/requirements/{self.req.id}/lead/create/', {
            'name': 'Lead 3 by DF2 after confirmed',
            'phone': '2222222222',
            'email': 'dup@test.com',
            'selected_items': [sub.id],
            f'count_{sub.id}': 1
        })
        self.assertEqual(res3.status_code, 302)
        # Should now be allowed, lead count becomes 3
        self.assertEqual(Lead.objects.filter(email='dup@test.com').count(), 3)


class HistoricalLeadHierarchyTestCase(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username='sa_hh@cyborg.com', email='sa_hh@cyborg.com', password='password123', usertype='superadmin'
        )
        self.dist1 = CustomUser.objects.create_user(
            username='dist1_hh', email='dist1_hh@cyborg.com', password='password123', usertype='district'
        )
        self.dist2 = CustomUser.objects.create_user(
            username='dist2_hh', email='dist2_hh@cyborg.com', password='password123', usertype='district'
        )
        self.fc1 = CustomUser.objects.create_user(
            username='fc1_hh', email='fc1_hh@cyborg.com', password='password123', usertype='mandalam', assigned_district=self.dist1
        )
        self.fc2 = CustomUser.objects.create_user(
            username='fc2_hh', email='fc2_hh@cyborg.com', password='password123', usertype='mandalam', assigned_district=self.dist2
        )
        self.df = CustomUser.objects.create_user(
            username='df_hh', email='df_hh@cyborg.com', password='password123', usertype='marketing', assigned_district=self.dist1, assigned_mandalam=self.fc1
        )
        self.req = CustomerRequirement.objects.create(
            customer=self.superadmin, title='Hierarchy Req', status='approved'
        )

    def test_df_edit_moves_pending_leads_leaves_confirmed_leads(self):
        client = Client()
        client.login(username='sa_hh@cyborg.com', password='password123')

        # Create 1 pending lead and 1 confirmed lead for DF under FC1 & Dist1
        pending_lead = Lead.objects.create(
            requirement=self.req, marketing_user=self.df, name='Pending Lead', phone='9999999991', status='pending'
        )
        confirmed_lead = Lead.objects.create(
            requirement=self.req, marketing_user=self.df, name='Confirmed Lead', phone='9999999992', status='confirmed'
        )

        self.assertEqual(pending_lead.assigned_mandalam, self.fc1)
        self.assertEqual(pending_lead.assigned_district, self.dist1)
        self.assertEqual(confirmed_lead.assigned_mandalam, self.fc1)
        self.assertEqual(confirmed_lead.assigned_district, self.dist1)

        # Edit DF to reassign to FC2 & Dist2
        res_edit = client.post(f'/superadmin/users/{self.df.id}/edit/', {
            'name': 'DF HH Updated',
            'usertype': 'marketing',
            'email': self.df.email,
            'assigned_mandalam': self.fc2.id
        })
        self.assertEqual(res_edit.status_code, 302)

        # Reload DF and leads
        self.df.refresh_from_db()
        pending_lead.refresh_from_db()
        confirmed_lead.refresh_from_db()

        self.assertEqual(self.df.assigned_mandalam, self.fc2)
        self.assertEqual(self.df.assigned_district, self.dist2)

        # Pending lead MUST move to FC2 & Dist2
        self.assertEqual(pending_lead.assigned_mandalam, self.fc2)
        self.assertEqual(pending_lead.assigned_district, self.dist2)

        # Confirmed lead MUST stay assigned to FC1 & Dist1
        self.assertEqual(confirmed_lead.assigned_mandalam, self.fc1)
        self.assertEqual(confirmed_lead.assigned_district, self.dist1)

    def test_fc_edit_moves_pending_leads_leaves_confirmed_leads(self):
        client = Client()
        client.login(username='sa_hh@cyborg.com', password='password123')

        # Create 1 pending lead and 1 confirmed lead under FC1 & Dist1
        pending_lead = Lead.objects.create(
            requirement=self.req, marketing_user=self.df, name='Pending Lead FC', phone='8888888881', status='pending'
        )
        confirmed_lead = Lead.objects.create(
            requirement=self.req, marketing_user=self.df, name='Confirmed Lead FC', phone='8888888882', status='confirmed'
        )

        # Edit FC1 to reassign to Dist2
        res_edit = client.post(f'/superadmin/users/{self.fc1.id}/edit/', {
            'name': 'FC1 HH Updated',
            'usertype': 'mandalam',
            'email': self.fc1.email,
            'assigned_district': self.dist2.id
        })
        self.assertEqual(res_edit.status_code, 302)

        self.fc1.refresh_from_db()
        self.df.refresh_from_db()
        pending_lead.refresh_from_db()
        confirmed_lead.refresh_from_db()

        # FC1 and DF assigned_district updated to Dist2
        self.assertEqual(self.fc1.assigned_district, self.dist2)
        self.assertEqual(self.df.assigned_district, self.dist2)

        # Pending lead moved to Dist2
        self.assertEqual(pending_lead.assigned_district, self.dist2)

        # Confirmed lead remains assigned to Dist1
        self.assertEqual(confirmed_lead.assigned_district, self.dist1)

    def test_non_superadmin_cannot_edit_fc_assigned_district(self):
        client = Client()
        # Login as District Franchise dist1
        client.login(username='dist1_hh', password='password123')

        # District franchise tries to edit FC1 to assign to dist2
        res = client.post(f'/superadmin/users/{self.fc1.id}/edit/', {
            'name': 'FC1 Edited by Dist',
            'usertype': 'mandalam',
            'email': self.fc1.email,
            'assigned_district': self.dist2.id
        })
        self.assertEqual(res.status_code, 302)
        self.fc1.refresh_from_db()
        # Verify assigned_district did NOT change to dist2
        self.assertEqual(self.fc1.assigned_district, self.dist1)
        self.assertEqual(self.fc1.name, 'FC1 Edited by Dist')

        # Superadmin edits FC1 to assign to dist2
        client.login(username='sa_hh@cyborg.com', password='password123')
        res_sa = client.post(f'/superadmin/users/{self.fc1.id}/edit/', {
            'name': 'FC1 Edited by SA',
            'usertype': 'mandalam',
            'email': self.fc1.email,
            'assigned_district': self.dist2.id
        })
        self.assertEqual(res_sa.status_code, 302)
        self.fc1.refresh_from_db()
        # Verify assigned_district changed to dist2
        self.assertEqual(self.fc1.assigned_district, self.dist2)

    def test_non_superadmin_cannot_edit_df_assigned_mandalam(self):
        client = Client()
        # Login as District Franchise dist1
        client.login(username='dist1_hh', password='password123')

        # District franchise tries to edit DF to assign to fc2
        res = client.post(f'/superadmin/users/{self.df.id}/edit/', {
            'name': 'DF Edited by Dist',
            'usertype': 'marketing',
            'email': self.df.email,
            'assigned_mandalam': self.fc2.id
        })
        self.assertEqual(res.status_code, 302)
        self.df.refresh_from_db()
        # Verify assigned_mandalam did NOT change to fc2
        self.assertEqual(self.df.assigned_mandalam, self.fc1)
        self.assertEqual(self.df.assigned_district, self.dist1)
        self.assertEqual(self.df.name, 'DF Edited by Dist')

        # Superadmin edits DF to assign to fc2
        client.login(username='sa_hh@cyborg.com', password='password123')
        res_sa = client.post(f'/superadmin/users/{self.df.id}/edit/', {
            'name': 'DF Edited by SA',
            'usertype': 'marketing',
            'email': self.df.email,
            'assigned_mandalam': self.fc2.id
        })
        self.assertEqual(res_sa.status_code, 302)
        self.df.refresh_from_db()
        # Verify assigned_mandalam changed to fc2 and assigned_district synced to dist2
        self.assertEqual(self.df.assigned_mandalam, self.fc2)
        self.assertEqual(self.df.assigned_district, self.dist2)

class ServerSideLeadsDataTableTestCase(TestCase):
    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username='sa_dt@cyborg.com',
            email='sa_dt@cyborg.com',
            password='password123',
            usertype='superadmin',
            name='Super Admin DT'
        )
        self.customer = CustomUser.objects.create_user(
            username='cust_dt@cyborg.com',
            email='cust_dt@cyborg.com',
            password='password123',
            usertype='customer',
            name='Customer DT'
        )
        self.df = CustomUser.objects.create_user(
            username='df_dt@cyborg.com',
            email='df_dt@cyborg.com',
            password='password123',
            usertype='marketing',
            name='DF DT'
        )
        self.category = Category.objects.create(name='General Services', cat_type='other')
        self.requirement = CustomerRequirement.objects.create(
            customer=self.customer,
            category=self.category,
            title='Website Development Requirement',
            status='approved'
        )
        self.lead = Lead.objects.create(
            name='ServerSide Lead 1',
            phone='9876543210',
            email='lead1@example.com',
            requirement=self.requirement,
            marketing_user=self.df,
            status='pending',
            current_level='superadmin'
        )

    def test_leads_datatable_ajax_response(self):
        client = Client()
        client.login(username='sa_dt@cyborg.com', password='password123')
        response = client.get('/leads/?draw=1&start=0&length=10', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertIn('draw', json_data)
        self.assertEqual(json_data['draw'], 1)
        self.assertEqual(json_data['recordsTotal'], 1)
        self.assertEqual(json_data['recordsFiltered'], 1)
        self.assertEqual(len(json_data['data']), 1)
        lead_row = json_data['data'][0]
        self.assertEqual(lead_row['id'], self.lead.id)
        self.assertEqual(lead_row['name'], 'ServerSide Lead 1')
        self.assertEqual(lead_row['DT_RowAttr']['data-is-controlled'], 'true')










