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
        
        # Verify withdrawal request was created
        wr = WithdrawalRequest.objects.get(request_type='gst')
        self.assertEqual(wr.amount, Decimal('100.00'))
        self.assertEqual(wr.status, 'pending')
        
        # Approve withdrawal request
        import json
        client.post(f'/wallet/requests/{wr.id}/update/', json.dumps({
            'status': 'approved',
            'remarks': 'Approved GST withdrawal'
        }), content_type='application/json')
        
        wr.refresh_from_db()
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
        
        # Verify withdrawal request was created
        wr = WithdrawalRequest.objects.get(request_type='expense')
        self.assertEqual(wr.amount, Decimal('50.00'))
        self.assertEqual(wr.status, 'pending')


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
            'purpose': 'Performance Bonus',
            f'amount_for_user_{self.marketing.id}': '150.00',
            f'amount_for_user_{self.district.id}': '250.00'
        }
        
        from cyborgapp.utils import get_or_create_wallet
        marketing_wallet = get_or_create_wallet(self.marketing)
        self.assertEqual(marketing_wallet.balance, Decimal('0.00'))

        response = client.post('/superadmin/incentives/create/', post_data)
        self.assertEqual(response.status_code, 302)

        from cyborgapp.models import Incentive
        self.assertEqual(Incentive.objects.filter(purpose='Performance Bonus').count(), 2)

        marketing_wallet.refresh_from_db()
        self.assertEqual(marketing_wallet.balance, Decimal('150.00'))

        district_wallet = get_or_create_wallet(self.district)
        self.assertEqual(district_wallet.balance, Decimal('250.00'))




