from django.contrib import admin
from .models import (
    CustomUser, Category, SubCategory, CustomerRequirement,
    RequirementItem, Lead, LeadItem, LeadUpdate, LeadAssociateUpdate,
    CommissionSetting, RegistrationCommission, Wallet, CommissionTransaction,
    WithdrawalRequest, RequirementAssignment, LeadInstallment, Incentive
)

@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ('username', 'name', 'email', 'usertype', 'is_active')
    list_filter = ('usertype', 'is_active')
    search_fields = ('username', 'name', 'email')
    
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal Info', {'fields': ('name', 'email', 'usertype', 'pass_word')}),
        ('Assignments', {'fields': ('assigned_district', 'assigned_mandalam', 'created_by', 'accessible_districts', 'assigned_facilitation_centers')}),
        ('Bank Account Details', {'fields': ('bank_account_number', 'bank_ifsc', 'bank_account_holder', 'bank_phone')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'cat_type', 'created_at')
    list_filter = ('cat_type',)
    search_fields = ('name',)

@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'is_mandatory_target', 'mandatory_target_count')
    list_filter = ('category', 'is_mandatory_target')
    search_fields = ('name',)

class RequirementItemInline(admin.TabularInline):
    model = RequirementItem
    extra = 1

@admin.register(CustomerRequirement)
class CustomerRequirementAdmin(admin.ModelAdmin):
    list_display = ('title', 'customer', 'status', 'created_at')
    list_filter = ('status', 'category')
    search_fields = ('title', 'customer__name')
    inlines = [RequirementItemInline]

class LeadItemInline(admin.TabularInline):
    model = LeadItem
    extra = 1

class LeadUpdateInline(admin.TabularInline):
    model = LeadUpdate
    extra = 0

@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('name', 'requirement', 'status', 'current_level', 'invoice_sent', 'created_at')
    list_filter = ('status', 'current_level', 'invoice_sent')
    search_fields = ('name', 'phone', 'requirement__title')
    inlines = [LeadItemInline, LeadUpdateInline]

@admin.register(CommissionSetting)
class CommissionSettingAdmin(admin.ModelAdmin):
    list_display = ('usertype', 'percentage', 'updated_at')

@admin.register(RegistrationCommission)
class RegistrationCommissionAdmin(admin.ModelAdmin):
    list_display = ('usertype', 'total_amount', 'superadmin_amount', 'district_amount', 'mandalam_amount')

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'total_earned', 'withdrawn_amount')
    search_fields = ('user__name', 'user__username')

@admin.register(CommissionTransaction)
class CommissionTransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'transaction_type', 'created_at')
    list_filter = ('transaction_type',)
    search_fields = ('user__name', 'description')

@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'status', 'account_number', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__name', 'account_number', 'account_holder')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Request Info', {'fields': ('user', 'amount', 'status', 'remarks')}),
        ('Bank Details (Used for this payout)', {
            'fields': ('account_number', 'ifsc_code', 'account_holder', 'phone_linked'),
            'description': 'These were the details provided by the user at the time of request.'
        }),
        ('Metadata', {'fields': ('created_at', 'updated_at')}),
    )

@admin.register(RequirementAssignment)
class RequirementAssignmentAdmin(admin.ModelAdmin):
    list_display = ('requirement_item', 'facilitation_center', 'assigned_count', 'assigned_by', 'created_at')
    list_filter = ('facilitation_center', 'assigned_by')
    search_fields = ('requirement_item__requirement__title', 'facilitation_center__name')

@admin.register(LeadInstallment)
class LeadInstallmentAdmin(admin.ModelAdmin):
    list_display = ('lead', 'installment_number', 'amount', 'status', 'invoice_sent', 'razorpay_payment_id', 'created_at')
    list_filter = ('status', 'invoice_sent')
    search_fields = ('lead__name', 'razorpay_payment_id')

@admin.register(LeadAssociateUpdate)
class LeadAssociateUpdateAdmin(admin.ModelAdmin):
    list_display = ('lead', 'user', 'created_at')
    search_fields = ('lead__name', 'user__name', 'update_text')
    list_filter = ('user__usertype',)

@admin.register(Incentive)
class IncentiveAdmin(admin.ModelAdmin):
    list_display = ('incentive_type', 'lead_from', 'lead_to', 'district_franchise_incentive', 'feciliattion_center_incentive', 'digital_franchise_incentive', 'is_active', 'created_by', 'created_at')
    list_filter = ('incentive_type', 'is_active', 'created_by')
    search_fields = ('incentive_type',)
