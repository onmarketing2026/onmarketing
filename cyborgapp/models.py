from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.conf import settings

class CustomUser(AbstractUser):
    USER_TYPE_CHOICES = (
        ('superadmin', 'Superadmin'),
        ('customer', 'Associate Company'),
        ('manager', 'Manager'),
        ('district', 'District Franchise'),
        ('mandalam', 'Fecilitation Center'),
        ('marketing', 'Digital Franchise'),
        ('staff', 'Staff'),
    )

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    usertype = models.CharField(max_length=50, choices=USER_TYPE_CHOICES, default='superadmin')
    pass_word = models.CharField(max_length=128, null=True, blank=True, help_text="Stores plain text password")

    # Hierarchical assignments
    assigned_district = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='district_users')
    assigned_mandalam = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='mandalam_users')
    created_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_users')
    
    # For Customers to see requirements from multiple districts
    accessible_districts = models.ManyToManyField('self', blank=True, symmetrical=False, related_name='customer_accessible_districts')

    # For Staff to monitor multiple Fecilitation Centers
    assigned_facilitation_centers = models.ManyToManyField('self', blank=True, symmetrical=False, related_name='staff_users')

    # Bank Account Details (Stored for pre-filling)
    bank_account_number = models.CharField(max_length=50, null=True, blank=True)
    bank_ifsc = models.CharField(max_length=20, null=True, blank=True)
    bank_account_holder = models.CharField(max_length=255, null=True, blank=True)
    bank_phone = models.CharField(max_length=20, null=True, blank=True)

    def __str__(self):
        return f"{self.username} ({self.get_usertype_display()})"

class Category(models.Model):
    TYPE_CHOICES = (
        ('count', 'Count'),
        ('other', 'Other'),
    )
    name = models.CharField(max_length=100)
    cat_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='other')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return self.name

from django.core.exceptions import ValidationError

class SubCategory(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='subcategories')
    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    is_mandatory_target = models.BooleanField(default=False, verbose_name="Is Mandatory Target")

    def clean(self):
        super().clean()
        from django.apps import apps
        RequirementAssignment = apps.get_model('cyborgapp', 'RequirementAssignment')

        if self.pk:
            old_sub = SubCategory.objects.get(pk=self.pk)
            if old_sub.is_mandatory_target != self.is_mandatory_target:
                if RequirementAssignment.objects.filter(requirement_item__subcategory=self).exists():
                    raise ValidationError(
                        "The mandatory status of this subcategory cannot be changed "
                        "because its requirements have already been assigned to facilitation centers."
                    )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def is_assigned_to_fc(self):
        from .models import RequirementAssignment
        return RequirementAssignment.objects.filter(requirement_item__subcategory=self).exists()

    def __str__(self):
        return f"{self.category.name} - {self.name}"

class CustomerRequirement(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
    )
    customer = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='requirements')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='requirements')
    title = models.CharField(max_length=255)
    description = models.TextField()
    image = models.ImageField(upload_to='requirements/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    customer_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    admin_markup = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    other_expenses = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    gst = models.DecimalField(max_digits=5, decimal_places=2, default=0.00) # GST percentage
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def get_customer_amount(self):
        items = self.items.all()
        if items.exists():
            return sum(item.customer_amount for item in items)
        return self.customer_amount

    @property
    def get_admin_markup(self):
        items = self.items.all()
        if items.exists():
            return sum(item.admin_markup for item in items)
        return self.admin_markup

    @property
    def get_other_expenses(self):
        items = self.items.all()
        if items.exists():
            return sum(item.other_expenses for item in items)
        return self.other_expenses

    @property
    def get_total_amount(self):
        items = self.items.all()
        if items.exists():
            return sum(item.total_amount for item in items)
        from decimal import Decimal
        base = self.customer_amount + self.admin_markup + self.other_expenses
        gst_amt = base * (self.gst / Decimal('100.00'))
        return base + gst_amt

    def __str__(self):
        return f"{self.title} - {self.customer.name}"

class RequirementItem(models.Model):
    requirement = models.ForeignKey(CustomerRequirement, on_delete=models.CASCADE, related_name='items')
    subcategory = models.ForeignKey(SubCategory, on_delete=models.CASCADE)
    count = models.IntegerField(default=0, null=True, blank=True)
    customer_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    admin_markup = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    other_expenses = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    gst = models.DecimalField(max_digits=5, decimal_places=2, default=0.00) # GST percentage
    mrp = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    image = models.ImageField(upload_to='requirement_items/', null=True, blank=True)
    youtube_link = models.URLField(max_length=500, null=True, blank=True)

    @property
    def total_amount(self):
        from decimal import Decimal
        base = self.customer_amount + self.admin_markup + self.other_expenses
        gst_amt = base * (self.gst / Decimal('100.00'))
        return base + gst_amt

    @property
    def total_mrp(self):
        from decimal import Decimal
        if not self.mrp:
            return Decimal('0.00')
        gst_amt = self.mrp * (self.gst / Decimal('100.00'))
        return self.mrp + gst_amt

    @property
    def get_sold_count(self):
        from .models import LeadItem
        from django.db.models import Sum
        return LeadItem.objects.filter(
            subcategory=self.subcategory,
            lead__requirement=self.requirement,
            lead__status='confirmed'
        ).aggregate(Sum('count'))['count__sum'] or 0

    @property
    def get_left_count(self):
        if not self.count: return 0
        return self.count - self.get_sold_count

    @property
    def get_total_assigned_count(self):
        from django.db.models import Sum
        return self.district_assignments.aggregate(Sum('assigned_count'))['assigned_count__sum'] or 0

    @property
    def get_remaining_assignable_count(self):
        if not self.count: return 0
        return self.count - self.get_total_assigned_count

    def __str__(self):
        return f"{self.subcategory.name} ({self.count})"

class Lead(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
    )
    LEVEL_CHOICES = (
        ('marketing', 'Marketing'),
        ('mandalam', 'Mandalam'),
        ('district', 'District'),
        ('superadmin', 'Superadmin'),
    )
    requirement = models.ForeignKey(CustomerRequirement, on_delete=models.CASCADE, related_name='leads')
    marketing_user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='added_leads')
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    email = models.EmailField(null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    current_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='marketing')
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    razorpay_payment_id = models.CharField(max_length=255, null=True, blank=True)
    payment_mode = models.CharField(max_length=20, default='single', choices=(('single', 'Single'), ('part', 'Part')))
    confirmed_by = models.ForeignKey(
        'CustomUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='confirmed_leads'
    )
    invoice_sent = models.BooleanField(default=False)  # Guard: prevent duplicate invoice emails
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def get_total_amount(self):
        if self.status == 'confirmed' and self.total_amount > 0:
            return self.total_amount
            
        total = 0
        for item in self.items.all():
            req_item = self.requirement.items.filter(subcategory=item.subcategory).first()
            if req_item:
                if self.requirement.category and self.requirement.category.cat_type == 'count':
                    total += req_item.total_amount * (item.count or 0)
                else:
                    total += req_item.total_amount
        return total

    @property
    def get_customer_amount(self):
        total = 0
        for item in self.items.all():
            req_item = self.requirement.items.filter(subcategory=item.subcategory).first()
            if req_item:
                qty = item.count if self.requirement.category and self.requirement.category.cat_type == 'count' else 1
                total += req_item.customer_amount * (qty or 1)
        return total

    @property
    def get_markup_amount(self):
        total = 0
        for item in self.items.all():
            req_item = self.requirement.items.filter(subcategory=item.subcategory).first()
            if req_item:
                qty = item.count if self.requirement.category and self.requirement.category.cat_type == 'count' else 1
                total += req_item.admin_markup * (qty or 1)
        return total

    @property
    def get_expense_amount(self):
        total = 0
        for item in self.items.all():
            req_item = self.requirement.items.filter(subcategory=item.subcategory).first()
            if req_item:
                qty = item.count if self.requirement.category and self.requirement.category.cat_type == 'count' else 1
                total += req_item.other_expenses * (qty or 1)
        return total

    @property
    def get_gst_amount(self):
        from decimal import Decimal
        total = 0
        for item in self.items.all():
            req_item = self.requirement.items.filter(subcategory=item.subcategory).first()
            if req_item:
                qty = item.count if self.requirement.category and self.requirement.category.cat_type == 'count' else 1
                base = req_item.customer_amount + req_item.admin_markup + req_item.other_expenses
                gst_amt = base * (req_item.gst / Decimal('100.00'))
                total += gst_amt * (qty or 1)
        return total

    @property
    def installment_pending(self):
        if hasattr(self, '_installment_pending'):
            return self._installment_pending
        if self.payment_mode == 'part':
            return self.installments.filter(status='pending').exists()
        return False

    @installment_pending.setter
    def installment_pending(self, value):
        self._installment_pending = value

    @property
    def has_any_payment(self):
        if self.payment_mode == 'single':
            return self.status == 'confirmed'
        elif self.payment_mode == 'part':
            return self.installments.filter(status='paid').exists()
        return False

    @property
    def get_req_items_with_fc_limits(self):
        fc = self.marketing_user.assigned_mandalam
        items_data = []
        for item in self.requirement.items.select_related('subcategory').all():
            count = item.count
            left = item.get_left_count
            if fc:
                from .models import RequirementAssignment
                asgn = RequirementAssignment.objects.filter(requirement_item=item, facilitation_center=fc).first()
                if asgn:
                    count = asgn.assigned_count
                    left = asgn.get_left_count
                else:
                    count = 0
                    left = 0
            items_data.append(f"{item.subcategory_id}:{item.subcategory.name}:{count}:{item.total_amount}:{left}")
        return "|".join(items_data)

    def __str__(self):
        return f"{self.name} - {self.requirement.title}"

class LeadItem(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='items')
    subcategory = models.ForeignKey(SubCategory, on_delete=models.CASCADE)
    count = models.IntegerField(default=0, null=True, blank=True)

    def __str__(self):
        return f"{self.subcategory.name} ({self.count})"
        return f"{self.subcategory.name} ({self.count})"

class LeadUpdate(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='updates')
    update_text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

class LeadAssociateUpdate(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='associate_updates')
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    update_text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

class CommissionSetting(models.Model):
    usertype = models.CharField(max_length=20, choices=CustomUser.USER_TYPE_CHOICES, unique=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_usertype_display()} - {self.percentage}%"

class RegistrationCommission(models.Model):
    usertype = models.CharField(max_length=20, choices=CustomUser.USER_TYPE_CHOICES, unique=True)
    superadmin_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    district_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    mandalam_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    def __str__(self):
        return f"{self.get_usertype_display()} Reg Commission"

class Wallet(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='wallet')
    total_earned = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    withdrawn_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.name}'s Wallet - {self.balance}"

class CommissionTransaction(models.Model):
    TRANSACTION_TYPES = (
        ('sale', 'Project Sale (to Customer)'),
        ('commission', 'Project Commission'),
        ('product_sale', 'Product Sale'),
        ('district_reg', 'District Registration'),
        ('mandalam_reg', 'Mandalam Registration'),
        ('marketer_reg', 'Marketer Registration'),
        ('incentive', 'Incentive'),
    )
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='commissions')
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    reference_id = models.IntegerField(null=True, blank=True) # ID of Lead or Registered User
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.name} - {self.amount} ({self.get_transaction_type_display()})"

class Incentive(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='incentives')
    purpose = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_incentives')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Incentive for {self.user.name or self.user.username} - {self.amount} ({self.purpose})"

class WithdrawalRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )
    TYPE_CHOICES = (
        ('wallet', 'Wallet'),
        ('gst', 'GST'),
        ('expense', 'Expense'),
    )
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='withdrawal_requests')
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    request_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='wallet')
    remarks = models.TextField(null=True, blank=True)
    
    # Bank Details at time of request
    account_number = models.CharField(max_length=50, null=True, blank=True)
    ifsc_code = models.CharField(max_length=20, null=True, blank=True)
    account_holder = models.CharField(max_length=255, null=True, blank=True)
    phone_linked = models.CharField(max_length=20, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.name} - {self.amount} ({self.status})"

class DistrictRequirementAssignment(models.Model):
    requirement_item = models.ForeignKey(RequirementItem, on_delete=models.CASCADE, related_name='district_assignments')
    district = models.ForeignKey(CustomUser, on_delete=models.CASCADE, limit_choices_to={'usertype': 'district'}, related_name='assigned_district_requirements')
    assigned_count = models.IntegerField(default=0)
    assigned_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE, null=True, blank=True, related_name='given_district_assignments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('requirement_item', 'district')

    def __str__(self):
        return f"{self.requirement_item.subcategory.name} -> {self.district.name}"

    @property
    def get_sold_count(self):
        from .models import LeadItem
        from django.db.models import Sum
        marketing_users = CustomUser.objects.filter(assigned_district=self.district, usertype='marketing')
        qs = LeadItem.objects.filter(
            subcategory=self.requirement_item.subcategory,
            lead__requirement=self.requirement_item.requirement,
            lead__status='confirmed',
            lead__marketing_user__in=marketing_users
        )
        if self.requirement_item.requirement.category and self.requirement_item.requirement.category.cat_type == 'count':
            return qs.aggregate(Sum('count'))['count__sum'] or 0
        return qs.count()

    @property
    def get_left_count(self):
        if not self.requirement_item.requirement.category or self.requirement_item.requirement.category.cat_type != 'count':
            if self.assigned_count > 0:
                return self.assigned_count - self.get_sold_count
            return 999999
        return self.assigned_count - self.get_sold_count


class RequirementAssignment(models.Model):
    requirement_item = models.ForeignKey(RequirementItem, on_delete=models.CASCADE, related_name='assignments')
    facilitation_center = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='item_assignments', limit_choices_to={'usertype': 'mandalam'})
    assigned_count = models.IntegerField(default=0)
    assigned_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE, null=True, blank=True, related_name='given_item_assignments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('requirement_item', 'facilitation_center')

    def __str__(self):
        return f"{self.requirement_item.subcategory.name} -> {self.facilitation_center.name}"

    @property
    def get_sold_count(self):
        from .models import LeadItem
        from django.db.models import Sum
        marketing_users = CustomUser.objects.filter(assigned_mandalam=self.facilitation_center, usertype='marketing')
        qs = LeadItem.objects.filter(
            subcategory=self.requirement_item.subcategory,
            lead__requirement=self.requirement_item.requirement,
            lead__status='confirmed',
            lead__marketing_user__in=marketing_users
        )
        if self.requirement_item.requirement.category and self.requirement_item.requirement.category.cat_type == 'count':
            return qs.aggregate(Sum('count'))['count__sum'] or 0
        return qs.count()

    @property
    def get_left_count(self):
        if not self.requirement_item.requirement.category or self.requirement_item.requirement.category.cat_type != 'count':
            if self.assigned_count > 0:
                return self.assigned_count - self.get_sold_count
            return 999999
        return self.assigned_count - self.get_sold_count

class LeadInstallment(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='installments')
    installment_number = models.IntegerField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=(('pending', 'Pending'), ('paid', 'Paid')), default='pending')
    razorpay_payment_id = models.CharField(max_length=255, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    invoice_sent = models.BooleanField(default=False)  # Guard: prevent duplicate invoice emails
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['installment_number']

    def __str__(self):
        return f"Lead {self.lead.id} - Installment {self.installment_number}: {self.amount} ({self.status})"


class Notification(models.Model):
    recipient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='notifications')
    actor = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='actions_performed', null=True, blank=True)
    verb = models.CharField(max_length=255)
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification for {self.recipient.username}: {self.verb}"


from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=Lead)
def check_fc_milestone_on_lead_confirm(sender, instance, created, **kwargs):
    if instance.status != 'confirmed':
        return

    # Check if this lead was confirmed. Let's find the subcategory for the lead.
    lead_items = instance.items.all()
    if not lead_items.exists():
        return

    for item in lead_items:
        sub = item.subcategory
        if not sub.is_mandatory_target:
            continue

        # Find the FC associated with this lead.
        m_user = instance.marketing_user
        if not m_user:
            continue

        fc = None
        if m_user.usertype == 'mandalam':
            fc = m_user
        elif m_user.assigned_mandalam:
            fc = m_user.assigned_mandalam

        if not fc:
            continue

        # Count confirmed leads for this subcategory for this FC
        from django.db.models import Q
        leads_qs = Lead.objects.filter(
            status='confirmed'
        ).filter(
            Q(marketing_user=fc) | Q(marketing_user__assigned_mandalam=fc)
        ).filter(
            items__subcategory=sub
        ).distinct()

        if leads_qs.count() == 20:
            # Check if this is the first time the FC achieved any mandatory target
            mandatory_subs = SubCategory.objects.filter(is_mandatory_target=True)
            other_achieved = False
            for other_sub in mandatory_subs:
                if other_sub != sub:
                    other_cnt = Lead.objects.filter(
                        status='confirmed'
                    ).filter(
                        Q(marketing_user=fc) | Q(marketing_user__assigned_mandalam=fc)
                    ).filter(
                        items__subcategory=other_sub
                    ).distinct().count()
                    if other_cnt >= 20:
                        other_achieved = True
                        break

            if not other_achieved:
                # FC has reached their 20th confirmed lead milestone!
                recipients = set()

                # Superadmins
                superadmins = CustomUser.objects.filter(usertype='superadmin')
                for sa in superadmins:
                    recipients.add(sa)

                # District Franchise
                if fc.assigned_district:
                    recipients.add(fc.assigned_district)

                # Managers corresponding to the FC's district
                if fc.assigned_district:
                    managers = CustomUser.objects.filter(usertype='manager', assigned_district=fc.assigned_district)
                    for mgr in managers:
                        recipients.add(mgr)

                # Send notifications
                verb = f"Facilitation Center {fc.name or fc.username} has achieved their 20-lead milestone on mandatory target: {sub.name}!"
                
                for recipient in recipients:
                    if not Notification.objects.filter(recipient=recipient, verb=verb).exists():
                        Notification.objects.create(
                            recipient=recipient,
                            actor=fc,
                            verb=verb,
                            lead=instance
                        )


