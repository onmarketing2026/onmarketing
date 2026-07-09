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
        inst_summary = f" (Installment {installment.installment_number})"
        if CommissionTransaction.objects.filter(reference_id=lead.id, description__contains=inst_summary).exists():
            return
        lead_total = Decimal(str(lead.get_total_amount))
        if lead_total > 0:
            ratio = Decimal(str(installment.amount)) / lead_total
        total_customer_amount = total_customer_amount * ratio
        total_markup_pool = total_markup_pool * ratio
    else:
        if CommissionTransaction.objects.filter(reference_id=lead.id, transaction_type__in=['sale', 'commission']).exclude(description__contains='Installment').exists():
            return

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
    fc_user = None
    if marketing_user:
        if marketing_user.usertype == 'mandalam':
            fc_user = marketing_user
        elif marketing_user.usertype == 'marketing':
            fc_user = marketing_user.assigned_mandalam

    mandalam_user = marketing_user.assigned_mandalam if marketing_user else None
    district_user = marketing_user.assigned_district if marketing_user else None
    superadmin = CustomUser.objects.filter(usertype='superadmin').first()

    # Determine if target is achieved by the associated FC
    target_achieved = True
    if fc_user and not has_fc_achieved_mandatory_target(fc_user, exclude_lead=lead):
        target_achieved = False

    pct_marketing = settings.get('marketing', Decimal('0.00'))
    pct_mandalam = settings.get('mandalam', Decimal('0.00'))
    pct_district = settings.get('district', Decimal('0.00'))
    pct_superadmin = settings.get('superadmin', Decimal('0.00'))

    if not target_achieved:
        # Divert marketing and mandalam shares to superadmin
        pct_superadmin += pct_marketing + pct_mandalam
        pct_marketing = Decimal('0.00')
        pct_mandalam = Decimal('0.00')

    payout_configs = [
        ('marketing', marketing_user, pct_marketing, "Marketing Commission"),
        ('mandalam', mandalam_user, pct_mandalam, "Mandalam Commission"),
        ('district', district_user, pct_district, "District Commission"),
        ('superadmin', superadmin, pct_superadmin, "Superadmin Share")
    ]

    for role, user, percentage, label in payout_configs:
        if user and percentage > 0:
            amount = (total_markup_pool * Decimal(str(percentage))) / Decimal('100')
            if amount > 0:
                add_to_wallet(
                    user=user,
                    amount=amount,
                    transaction_type='commission',
                    reference_id=str(lead.id),
                    description=f"{label} from {lead.requirement.title}{item_summary}{inst_summary}"
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
        
        # Check target achievement for the associated mandalam
        target_achieved = True
        if mandalam and not has_fc_achieved_mandatory_target(mandalam):
            target_achieved = False
            
        if target_achieved:
            if mandalam and config.mandalam_amount > 0:
                add_to_wallet(mandalam, config.mandalam_amount, 'marketer_reg', new_user.id, f"Commission from Marketer registration: {new_user.name}")
            if superadmin and config.superadmin_amount > 0:
                add_to_wallet(superadmin, config.superadmin_amount, 'marketer_reg', new_user.id, f"Commission from Marketer registration: {new_user.name}")
        else:
            # If target not achieved, mandalam share goes to superadmin
            total_superadmin_amount = config.superadmin_amount + config.mandalam_amount
            if superadmin and total_superadmin_amount > 0:
                add_to_wallet(superadmin, total_superadmin_amount, 'marketer_reg', new_user.id, f"Commission from Marketer registration (Redirected): {new_user.name}")
                
        if district and config.district_amount > 0:
            add_to_wallet(district, config.district_amount, 'marketer_reg', new_user.id, f"Commission from Marketer registration: {new_user.name}")

def has_fc_achieved_mandatory_target(fc_user, exclude_lead=None):
    """
    Checks if a facilitation center (mandalam user) has achieved at least 20 confirmed leads
    for ANY of the subcategories marked as `is_mandatory_target=True`.
    """
    if not fc_user or fc_user.usertype != 'mandalam':
        return True
    
    from .models import SubCategory
    mandatory_subs = SubCategory.objects.filter(is_mandatory_target=True)
    if not mandatory_subs.exists():
        # If no mandatory subcategory has been designated yet, do not block commissions or assignments.
        return True
        
    from .models import Lead
    from django.db.models import Q
    
    # We count leads associated with this facilitation center (fc_user):
    # either created by the fc_user themselves, or created by marketing users under them.
    for sub in mandatory_subs:
        leads_qs = Lead.objects.filter(
            status='confirmed'
        ).filter(
            Q(marketing_user=fc_user) | Q(marketing_user__assigned_mandalam=fc_user)
        ).filter(
            items__subcategory=sub
        )
        
        if exclude_lead:
            leads_qs = leads_qs.exclude(id=exclude_lead.id)
        
        # Exclude leads in "payment pending" status (part-payment mode where any installment is still pending)
        leads_qs = leads_qs.exclude(
            payment_mode='part',
            installments__status='pending'
        ).distinct()
        
        if leads_qs.count() >= 20:
            return True
            
    return False
