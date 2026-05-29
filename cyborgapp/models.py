from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone

class CustomUser(AbstractUser):
    USER_TYPE_CHOICES = (
        ('superadmin', 'Superadmin'),
        ('customer', 'Associate Company'),
        ('manager', 'Manager'),
        ('district', 'District Franchise'),
        ('mandalam', 'Fecilitation Center'),
        ('marketing', 'Digital Franchise'),
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

class SubCategory(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='subcategories')
    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)

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
    def get_total_amount(self):
        return self.get_customer_amount + self.get_admin_markup

    def __str__(self):
        return f"{self.title} - {self.customer.name}"

class RequirementItem(models.Model):
    requirement = models.ForeignKey(CustomerRequirement, on_delete=models.CASCADE, related_name='items')
    subcategory = models.ForeignKey(SubCategory, on_delete=models.CASCADE)
    count = models.IntegerField(default=0, null=True, blank=True)
    customer_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    admin_markup = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    description = models.TextField(null=True, blank=True)
    image = models.ImageField(upload_to='requirement_items/', null=True, blank=True)

    @property
    def total_amount(self):
        return self.customer_amount + self.admin_markup

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
    )
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='commissions')
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    reference_id = models.IntegerField(null=True, blank=True) # ID of Lead or Registered User
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.name} - {self.amount} ({self.get_transaction_type_display()})"

class WithdrawalRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='withdrawal_requests')
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
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
