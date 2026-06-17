from .models import Wallet, CommissionTransaction, CommissionSetting, CustomUser
from decimal import Decimal
from django.db import transaction

def get_or_create_wallet(user):
    wallet, created = Wallet.objects.get_or_create(user=user)
    return wallet

def add_to_wallet(user, amount, transaction_type, reference_id, description):
    from decimal import Decimal
    with transaction.atomic():
        wallet = get_or_create_wallet(user)
        # Ensure we are working with Decimals to avoid float errors
        amount_decimal = Decimal(str(amount))
        wallet.total_earned = Decimal(str(wallet.total_earned)) + amount_decimal
        wallet.balance = Decimal(str(wallet.balance)) + amount_decimal
        wallet.save()
        
        CommissionTransaction.objects.create(
            user=user,
            amount=amount,
            transaction_type=transaction_type,
            reference_id=reference_id,
            description=description
        )

def distribute_product_sale_commission(lead, installment=None):
    from decimal import Decimal
    # Calculate amounts based on lead's specific items
    total_customer_amount = Decimal('0.00')
    total_markup_pool = Decimal('0.00')
    
    lead_items = lead.items.all()
    cat_type = lead.requirement.category.cat_type if lead.requirement.category else 'other'
    
    if lead_items.exists():
        for l_item in lead_items:
            req_item = lead.requirement.items.filter(subcategory=l_item.subcategory).first()
            if req_item:
                qty = Decimal(str(l_item.count or 1)) if cat_type == 'count' else Decimal('1')
                total_customer_amount += Decimal(str(req_item.customer_amount)) * qty
                total_markup_pool += Decimal(str(req_item.admin_markup)) * qty

    # Calculate proportional ratio if this is an installment payment
    ratio = Decimal('1.00')
    inst_summary = ""
    if installment:
        lead_total = Decimal(str(lead.get_total_amount))
        if lead_total > 0:
            ratio = Decimal(str(installment.amount)) / lead_total
        total_customer_amount = total_customer_amount * ratio
        total_markup_pool = total_markup_pool * ratio
        inst_summary = f" (Installment {installment.installment_number})"

    # Prepare item count summary for description if applicable
    item_summary = ""
    if cat_type == 'count':
        counts = [f"{li.subcategory.name} x{li.count}" for li in lead_items]
        item_summary = f" [Items: {', '.join(counts)}]"

    # 1. Pay the Customer their base amount
    if total_customer_amount > 0:
        add_to_wallet(
            user=lead.requirement.customer,
            amount=total_customer_amount,
            transaction_type='sale',
            reference_id=str(lead.id),
            description=f"Payment for project: {lead.requirement.title} (Lead: {lead.name}){item_summary}{inst_summary}"
        )

    if total_markup_pool <= 0:
        return

    # 2. Split the Admin Markup among the hierarchy
    settings_objs = CommissionSetting.objects.all()
    settings = {s.usertype: s.percentage for s in settings_objs}
    
    marketing_user = lead.marketing_user
    mandalam_user = marketing_user.assigned_mandalam if marketing_user else None
    district_user = marketing_user.assigned_district if marketing_user else None
    superadmin = CustomUser.objects.filter(usertype='superadmin').first()

    payouts = [
        ('marketing', marketing_user, "Marketing Commission"),
        ('mandalam', mandalam_user, "Mandalam Commission"),
        ('district', district_user, "District Commission"),
        ('superadmin', superadmin, "Superadmin Share")
    ]

    for role, user, label in payouts:
        if user:
            percentage = settings.get(role, Decimal('0.00'))
            if percentage > 0:
                amount = (total_markup_pool * Decimal(str(percentage))) / Decimal('100')
                if amount > 0:
                    add_to_wallet(
                        user=user,
                        amount=amount,
                        transaction_type='commission',
                        reference_id=str(lead.id),
                        description=f"{label} from {lead.requirement.title} (Pool: ₹{total_markup_pool}){item_summary}{inst_summary}"
                    )

def handle_registration_commission(new_user):
    from .models import RegistrationCommission
    superadmin = CustomUser.objects.filter(usertype='superadmin').first()
    
    # Get configuration for this usertype
    try:
        config = RegistrationCommission.objects.get(usertype=new_user.usertype)
    except RegistrationCommission.DoesNotExist:
        return # No commission configured for this usertype

    if new_user.usertype == 'district':
        # Superadmin gets configured amount
        if superadmin and config.superadmin_amount > 0:
            add_to_wallet(superadmin, config.superadmin_amount, 'district_reg', new_user.id, f"Commission from District registration: {new_user.name}")
            
    elif new_user.usertype == 'mandalam':
        # Configured shares to District and Superadmin
        district = new_user.assigned_district
        if district and config.district_amount > 0:
            add_to_wallet(district, config.district_amount, 'mandalam_reg', new_user.id, f"Commission from Mandalam registration: {new_user.name}")
        if superadmin and config.superadmin_amount > 0:
            add_to_wallet(superadmin, config.superadmin_amount, 'mandalam_reg', new_user.id, f"Commission from Mandalam registration: {new_user.name}")
            
    elif new_user.usertype == 'marketing':
        # Configured shares to Mandalam, District, and Superadmin
        mandalam = new_user.assigned_mandalam
        district = new_user.assigned_district
        
        if mandalam and config.mandalam_amount > 0:
            add_to_wallet(mandalam, config.mandalam_amount, 'marketer_reg', new_user.id, f"Commission from Marketer registration: {new_user.name}")
        if district and config.district_amount > 0:
            add_to_wallet(district, config.district_amount, 'marketer_reg', new_user.id, f"Commission from Marketer registration: {new_user.name}")
        if superadmin and config.superadmin_amount > 0:
            add_to_wallet(superadmin, config.superadmin_amount, 'marketer_reg', new_user.id, f"Commission from Marketer registration: {new_user.name}")
