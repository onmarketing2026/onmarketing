from django import forms
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import IntegrityError, models
from .models import CustomUser, CustomerRequirement, Lead, LeadItem, LeadUpdate, CommissionSetting

@login_required(login_url='login')
def commission_settings(request):
    if request.user.usertype != 'superadmin':
        messages.error(request, 'Permission denied.')
        return redirect('superadmin_dashboard')
        
    from .models import RegistrationCommission
    
    if request.method == 'POST':
        # Save Percentage Settings
        for utype in ['superadmin', 'district', 'mandalam', 'marketing']:
            percentage = request.POST.get(f'percentage_{utype}', 0)
            CommissionSetting.objects.update_or_create(
                usertype=utype,
                defaults={'percentage': percentage}
            )
            
        # Save Registration Settings
        for utype in ['district', 'mandalam', 'marketing']:
            total = request.POST.get(f'reg_total_{utype}', 0)
            s_amt = request.POST.get(f'reg_super_{utype}', 0)
            
            # Special case for District: Total goes to Superadmin
            if utype == 'district':
                s_amt = total
                
            d_amt = request.POST.get(f'reg_dist_{utype}', 0)
            m_amt = request.POST.get(f'reg_mand_{utype}', 0)
            
            RegistrationCommission.objects.update_or_create(
                usertype=utype,
                defaults={
                    'total_amount': total if total else 0,
                    'superadmin_amount': s_amt if s_amt else 0,
                    'district_amount': d_amt if d_amt else 0,
                    'mandalam_amount': m_amt if m_amt else 0
                }
            )
            
        messages.success(request, 'Commission settings updated successfully!')
        return redirect('commission_settings')
        
    settings_list = CommissionSetting.objects.all()
    reg_list = RegistrationCommission.objects.all()
    
    context = {
        'superadmin_comm': settings_list.filter(usertype='superadmin').first(),
        'district_comm': settings_list.filter(usertype='district').first(),
        'mandalam_comm': settings_list.filter(usertype='mandalam').first(),
        'marketing_comm': settings_list.filter(usertype='marketing').first(),
        
        'district_reg': reg_list.filter(usertype='district').first(),
        'mandalam_reg': reg_list.filter(usertype='mandalam').first(),
        'marketing_reg': reg_list.filter(usertype='marketing').first(),
    }
    return render(request, 'cyborgapp/superadmin/commissions.html', context)

def login_view(request):
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        remember = request.POST.get('remember')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            login(request, user)
            
            if remember:
                # Session lasts 2 weeks (1209600 seconds)
                request.session.set_expiry(1209600)
            else:
                # Session expires when browser closes
                request.session.set_expiry(0)
                
            if user.usertype == 'superadmin':
                return redirect('superadmin_dashboard')
            # Add other role redirects later as needed
            return redirect('superadmin_dashboard') # Default redirect for now
        else:
            messages.error(request, 'Invalid credentials.')
            
    return render(request, 'cyborgapp/login.html')

def logout_view(request):
    logout(request)
    return redirect('login')

from django.db.models import Sum, F, Count, Q
from .models import CustomUser, CustomerRequirement, Lead, LeadItem, LeadUpdate, CommissionSetting, CommissionTransaction, RegistrationCommission, WithdrawalRequest, Wallet

@login_required(login_url='login')
def superadmin_dashboard(request):
    user = request.user
    stats = {}
    
    if user.usertype == 'superadmin':
        # Total Sales: Confirmed leads total amount (static field)
        stats['total_sales'] = Lead.objects.filter(status='confirmed').aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        
        # Commission Payout: Total amount released to hierarchy (excluding superadmin and customer)
        stats['commission_payout'] = CommissionTransaction.objects.exclude(
            user__usertype__in=['superadmin', 'customer']
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Total Status: Total Customers
        stats['total_customers'] = CustomUser.objects.filter(usertype='customer').count()
        
        # Total Franchise: Total Active Marketing users
        stats['total_franchise'] = CustomUser.objects.filter(usertype='marketing', is_active=True).count()
        
    elif user.usertype in ['district', 'manager']:
        target_district = user if user.usertype == 'district' else user.assigned_district
        # Total Sales: Leads added by users in this district (marketers, mandalams, or district himself)
        confirmed_leads = Lead.objects.filter(
            status='confirmed'
        ).filter(
            Q(marketing_user__assigned_district=target_district) | Q(marketing_user=target_district)
        )
        stats['total_sales'] = confirmed_leads.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        stats['total_franchise'] = CustomUser.objects.filter(
            usertype='marketing', assigned_district=target_district, is_active=True
        ).count()
        
    elif user.usertype == 'mandalam':
        # Total Sales: Leads added by users in this mandalam (marketers or mandalam himself)
        confirmed_leads = Lead.objects.filter(
            status='confirmed'
        ).filter(
            Q(marketing_user__assigned_mandalam=user) | Q(marketing_user=user)
        )
        stats['total_sales'] = confirmed_leads.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        stats['total_franchise'] = CustomUser.objects.filter(
            usertype='marketing', assigned_mandalam=user, is_active=True
        ).count()
        
    elif user.usertype == 'marketing':
        # Total Sales: Leads added by this marketing user
        confirmed_leads = Lead.objects.filter(status='confirmed', marketing_user=user)
        stats['total_sales'] = confirmed_leads.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        
    elif user.usertype == 'customer':
        stats['total_requirements'] = CustomerRequirement.objects.filter(customer=user).count()

    return render(request, 'cyborgapp/superadmin/dashboard.html', {'stats': stats})

from django.contrib.auth.forms import PasswordResetForm

class CustomPasswordResetForm(PasswordResetForm):
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError("No user found with this email address.")
        return email

@login_required(login_url='login')
def profile_view(request):
    user = request.user
    
    # Define bank_user correctly for all requests
    bank_user = user
    if user.usertype == 'manager' and user.assigned_district:
        bank_user = user.assigned_district
        
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        new_password = request.POST.get('password')
        
        # Email validation (exclude current user)
        if CustomUser.objects.filter(email=email).exclude(id=user.id).exists():
            messages.error(request, 'This email is already in use by another account.')
            return redirect('profile')
            
        user.name = name
        user.email = email
        user.username = email # Email is username
        
        if new_password:
            user.set_password(new_password)
            user.pass_word = new_password # Keeping plain text as per existing pattern
            
        user.save()
        
        # Re-authenticate to keep the session alive after password change
        if new_password:
            login(request, user)
            
        messages.success(request, 'Profile updated successfully!')
        return redirect('profile')
        
    return render(request, 'cyborgapp/profile.html', {
        'user': user,
        'bank_user': bank_user
    })

@login_required(login_url='login')
def update_bank_details(request):
    if request.method == 'POST':
        target_user = request.user
        if target_user.usertype == 'manager' and target_user.assigned_district:
            target_user = target_user.assigned_district
            
        target_user.bank_account_number = request.POST.get('account_number')
        target_user.bank_ifsc = request.POST.get('ifsc_code')
        target_user.bank_account_holder = request.POST.get('account_holder')
        target_user.bank_phone = request.POST.get('phone_linked')
        target_user.save()
        messages.success(request, 'Bank details updated successfully.')
    return redirect('profile')

from django.contrib.auth import views as auth_views

class CustomPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    def form_valid(self, form):
        # This is where the password is saved
        user = form.save() 
        # Also save to our custom pass_word field in plain text
        user.pass_word = form.cleaned_data.get('new_password1')
        user.save()
        return super().form_valid(form)

@login_required(login_url='login')
def superadmin_users(request):
    current_user = request.user
    selected_usertype = request.GET.get('usertype')
    
    if current_user.usertype == 'superadmin':
        users = CustomUser.objects.exclude(usertype='superadmin').order_by('-id')
    elif current_user.usertype == 'district':
        # Districts see users assigned to them (Managers, Mandalams, Marketing under them)
        users = CustomUser.objects.filter(assigned_district=current_user).exclude(id=current_user.id).order_by('-id')
    elif current_user.usertype == 'manager':
        # Managers see all users belonging to their assigned district
        if current_user.assigned_district:
            users = CustomUser.objects.filter(assigned_district=current_user.assigned_district).exclude(id=current_user.id).order_by('-id')
        else:
            users = CustomUser.objects.filter(created_by=current_user).order_by('-id')
    elif current_user.usertype == 'mandalam':
        # Mandalams see all marketing users assigned to their mandalam
        users = CustomUser.objects.filter(assigned_mandalam=current_user, usertype='marketing').order_by('-id')
    else:
        users = CustomUser.objects.none()

    # Rule: Associate Company (customer) needed only for superadmin
    if current_user.usertype != 'superadmin':
        users = users.exclude(usertype='customer')
        if selected_usertype == 'customer':
            selected_usertype = None

    # Rule: Manager for super admin and district franchise
    if current_user.usertype not in ['superadmin', 'district']:
        users = users.exclude(usertype='manager')
        if selected_usertype == 'manager':
            selected_usertype = None

    if selected_usertype:
        users = users.filter(usertype=selected_usertype)

    # Check if AJAX DataTable request
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('draw'):
        draw = int(request.GET.get('draw', 1))
        start = int(request.GET.get('start', 0))
        length = int(request.GET.get('length', 10))
        search_value = request.GET.get('search[value]', '')
        
        # Base count (before search/filtering)
        records_total = users.count()
        
        # Apply search filter
        if search_value:
            from django.db.models import Q
            users = users.filter(
                Q(name__icontains=search_value) |
                Q(email__icontains=search_value) |
                Q(usertype__icontains=search_value) |
                Q(created_by__name__icontains=search_value)
            )
        
        records_filtered = users.count()
        
        # Sorting
        order_column_index = int(request.GET.get('order[0][column]', 0))
        order_dir = request.GET.get('order[0][dir]', 'asc')
        
        # Map column index to field
        columns_map = {
            0: 'name',
            1: 'email',
            2: 'usertype',
            3: 'created_by__name',
            4: 'is_active',
        }
        sort_field = columns_map.get(order_column_index, 'id')
        if order_dir == 'desc':
            sort_field = f'-{sort_field}'
            
        users = users.order_by(sort_field)
        
        # Pagination
        users_slice = users[start:start+length]
        
        # Serialize data
        data = []
        for u in users_slice:
            acc_dists = ",".join([str(d.id) for d in u.accessible_districts.all()])
            data.append({
                'id': u.id,
                'name': u.name,
                'email': u.email,
                'usertype': u.usertype,
                'usertype_display': u.get_usertype_display(),
                'created_by': u.created_by.name if u.created_by else 'Superadmin',
                'is_active': u.is_active,
                'assigned_district_id': u.assigned_district_id or '',
                'assigned_mandalam_id': u.assigned_mandalam_id or '',
                'accessible_districts': acc_dists
            })
            
        return JsonResponse({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data
        })

    districts = CustomUser.objects.filter(usertype='district', is_active=True)
    mandalams = CustomUser.objects.filter(usertype='mandalam')
    
    # If a district/manager is logged in, only show relevant mandalams for creation
    if current_user.usertype == 'district':
        mandalams = mandalams.filter(assigned_district=current_user)
    elif current_user.usertype == 'manager' and current_user.assigned_district:
        mandalams = mandalams.filter(assigned_district=current_user.assigned_district)
    elif current_user.usertype == 'mandalam':
        mandalams = CustomUser.objects.filter(id=current_user.id)

    return render(request, 'cyborgapp/superadmin/users.html', {
        'users': users,
        'districts': districts,
        'mandalams': mandalams,
        'selected_usertype': selected_usertype
    })

@login_required(login_url='login')
def superadmin_user_create(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        usertype = request.POST.get('usertype')
        password = request.POST.get('password')
        
        assigned_district_id = request.POST.get('assigned_district')
        assigned_mandalam_id = request.POST.get('assigned_mandalam')
        
        current_user = request.user
        
        # Rule validation
        if usertype == 'customer' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Only Superadmin can create Associate Company.')
            return redirect('superadmin_users')
            
        if usertype == 'manager' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Only Superadmin can create Manager.')
            return redirect('superadmin_users')
            
        try:
            # Base creation
            user = CustomUser.objects.create_user(
                username=email,
                email=email,
                name=name,
                usertype=usertype,
                password=password,
                pass_word=password,
                created_by=current_user,
                is_active=False,
            )
            
            # Hierarchy logic
            if current_user.usertype == 'superadmin':
                if usertype in ['manager', 'district', 'customer', 'mandalam'] and assigned_district_id:
                    user.assigned_district_id = assigned_district_id
                elif usertype == 'marketing' and assigned_mandalam_id:
                    mandalam_user = CustomUser.objects.get(id=assigned_mandalam_id)
                    user.assigned_mandalam = mandalam_user
                    if mandalam_user.assigned_district:
                        user.assigned_district = mandalam_user.assigned_district
                        
            elif current_user.usertype == 'district':
                user.assigned_district = current_user
                if usertype == 'marketing' and assigned_mandalam_id:
                    user.assigned_mandalam_id = assigned_mandalam_id
                    
            elif current_user.usertype == 'manager':
                if current_user.assigned_district:
                    user.assigned_district = current_user.assigned_district
                if usertype == 'marketing' and assigned_mandalam_id:
                    user.assigned_mandalam_id = assigned_mandalam_id
                    
            elif current_user.usertype == 'mandalam':
                user.assigned_mandalam = current_user
                if current_user.assigned_district:
                    user.assigned_district = current_user.assigned_district
            
            # Handle accessible districts for Customer
            if usertype == 'customer':
                district_ids = request.POST.getlist('accessible_districts')
                if district_ids:
                    user.accessible_districts.set(district_ids)
                        
            user.save()
            
            user.save()
            messages.success(request, 'User created successfully!')
            return redirect(f"/superadmin/users/?usertype={usertype}")
            
        except IntegrityError:
            messages.error(request, 'Error: A user with this email already exists.')
        except Exception as e:
            messages.error(request, f'Error creating user: {str(e)}')
            
    usertype_filter = request.POST.get('usertype', '')
    if usertype_filter:
        return redirect(f"/superadmin/users/?usertype={usertype_filter}")
    return redirect('superadmin_users')

@login_required(login_url='login')
def superadmin_user_edit(request, user_id):
    user = get_object_or_404(CustomUser, id=user_id)
    current_user = request.user
    
    # Permission validation
    if user.usertype == 'customer' and current_user.usertype != 'superadmin':
        messages.error(request, 'Permission denied: Only Superadmin can edit Associate Company.')
        return redirect('superadmin_users')
        
    if user.usertype == 'manager' and current_user.usertype != 'superadmin':
        messages.error(request, 'Permission denied: Only Superadmin can edit Manager.')
        return redirect('superadmin_users')
        
    if request.method == 'POST':
        new_usertype = request.POST.get('usertype')
        if new_usertype == 'customer' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Cannot assign Associate Company role.')
            return redirect('superadmin_users')
        if new_usertype == 'manager' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Cannot assign Manager role.')
            return redirect('superadmin_users')
            
        user.name = request.POST.get('name')
        user.usertype = new_usertype
        
        email = request.POST.get('email')
        if email:
            user.email = email
            user.username = email # Syncing username with email

        password = request.POST.get('password')
        if password: # only update if provided
            user.set_password(password)
            user.pass_word = password
            
        assigned_district_id = request.POST.get('assigned_district')
        assigned_mandalam_id = request.POST.get('assigned_mandalam')
        
        # Reset assignments
        user.assigned_district = None
        user.assigned_mandalam = None
        
        if user.usertype == 'manager' and assigned_district_id:
            user.assigned_district_id = assigned_district_id
        elif user.usertype == 'mandalam' and assigned_district_id:
            user.assigned_district_id = assigned_district_id
        elif user.usertype == 'marketing' and assigned_mandalam_id:
            mandalam_user = CustomUser.objects.get(id=assigned_mandalam_id)
            user.assigned_mandalam = mandalam_user
            if mandalam_user.assigned_district:
                user.assigned_district = mandalam_user.assigned_district
                
        # Handle accessible districts for Customer
        if user.usertype == 'customer':
            district_ids = request.POST.getlist('accessible_districts')
            user.accessible_districts.set(district_ids)
                
        try:
            user.save()
            messages.success(request, 'User updated successfully!')
        except IntegrityError:
            messages.error(request, 'Error: A user with this email already exists.')
        except Exception as e:
            messages.error(request, f'Error updating user: {str(e)}')
            
        return redirect(f"/superadmin/users/?usertype={user.usertype}")

    districts = CustomUser.objects.filter(usertype='district', is_active=True)
    mandalams = CustomUser.objects.filter(usertype='mandalam')
    return render(request, 'cyborgapp/superadmin/user_form.html', {
        'edit_user': user,
        'districts': districts,
        'mandalams': mandalams
    })

@login_required(login_url='login')
def superadmin_user_delete(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(CustomUser, id=user_id)
        current_user = request.user
        
        # Rule check
        if user.usertype == 'customer' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Only Superadmin can delete Associate Company.')
            return redirect('superadmin_users')
            
        if user.usertype == 'manager' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Only Superadmin can delete Manager.')
            return redirect('superadmin_users')
            
        user.delete()
        messages.success(request, 'User deleted successfully!')
        return redirect(f"/superadmin/users/?usertype={user.usertype}")
    return redirect('superadmin_users')

@login_required(login_url='login')
def superadmin_user_toggle_status(request, user_id):
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        with_payment = data.get('with_payment', False)
        
        user = get_object_or_404(CustomUser, id=user_id)
        current_user = request.user
        
        # Rule check
        if user.usertype == 'customer' and current_user.usertype != 'superadmin':
            return JsonResponse({'status': 'error', 'message': 'Permission denied: Only Superadmin can toggle Associate Company status.'})
            
        if user.usertype == 'manager' and current_user.usertype != 'superadmin':
            return JsonResponse({'status': 'error', 'message': 'Permission denied: Only Superadmin can toggle Manager status.'})
        
        # New Rule: Restrict manager activation if district is inactive
        if not user.is_active: # If we are trying to ACTIVATE
            if user.usertype == 'manager' and user.assigned_district and not user.assigned_district.is_active:
                return JsonResponse({
                    'status': 'error', 
                    'message': f'Cannot activate manager because district ({user.assigned_district.name}) is inactive.'
                }, status=400)
                
            # Razorpay integration for Digital Franchise (marketing)
            if user.usertype == 'marketing' and with_payment:
                razorpay_payment_id = data.get('razorpay_payment_id')
                razorpay_order_id = data.get('razorpay_order_id')
                razorpay_signature = data.get('razorpay_signature')
                
                from .models import RegistrationCommission
                try:
                    config = RegistrationCommission.objects.get(usertype='marketing')
                    amount = int(config.total_amount * 100) # Amount in paise
                except RegistrationCommission.DoesNotExist:
                    amount = 0
                    
                if amount > 0:
                    import razorpay
                    from django.conf import settings
                    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
                    
                    if not razorpay_payment_id:
                        # Create Razorpay order and return payment_required
                        order_data = {
                            'amount': amount,
                            'currency': 'INR',
                            'receipt': f'reg_{user.id}',
                            'notes': {
                                'user_id': user.id,
                                'project': 'onmarketing'
                            }
                        }
                        razorpay_order = client.order.create(data=order_data)
                        return JsonResponse({
                            'status': 'payment_required',
                            'key': settings.RAZORPAY_KEY_ID,
                            'amount': amount,
                            'order_id': razorpay_order['id'],
                            'user_name': user.name,
                            'user_email': user.email
                        })
                    else:
                        # Verify payment signature
                        try:
                            client.utility.verify_payment_signature({
                                'razorpay_order_id': razorpay_order_id,
                                'razorpay_payment_id': razorpay_payment_id,
                                'razorpay_signature': razorpay_signature
                            })
                        except razorpay.errors.SignatureVerificationError:
                            return JsonResponse({'status': 'error', 'message': 'Payment signature verification failed'})

        old_status = user.is_active
        user.is_active = not user.is_active
        
        # Commission logic: only when activating and with_payment is true
        if user.is_active and not old_status and with_payment:
            from .utils import handle_registration_commission
            handle_registration_commission(user)
            
        user.save()

        # Cascade deactivation: if district is deactivated, deactivate all its managers
        if not user.is_active and user.usertype == 'district':
            CustomUser.objects.filter(assigned_district=user, usertype='manager').update(is_active=False)
            
        return JsonResponse({'status': 'success', 'is_active': user.is_active})
    return JsonResponse({'status': 'error'}, status=400)

@login_required(login_url='login')
def check_email(request):
    email = request.GET.get('email', None)
    user_id = request.GET.get('user_id', None)
    if email:
        qs = CustomUser.objects.filter(email__iexact=email)
        if user_id:
            qs = qs.exclude(id=user_id)
        if qs.exists():
            return JsonResponse({'exists': True})
    return JsonResponse({'exists': False})

@login_required(login_url='login')
def requirement_list(request):
    current_user = request.user
    if current_user.usertype == 'superadmin':
        requirements = CustomerRequirement.objects.all().order_by('-created_at')
    elif current_user.usertype == 'customer':
        requirements = CustomerRequirement.objects.filter(customer=current_user).order_by('-created_at')
    elif current_user.usertype in ['district', 'manager', 'mandalam', 'marketing']:
        # Only show approved requirements for the assigned/accessible districts
        if current_user.usertype == 'district':
            districts = [current_user]
        elif current_user.assigned_district:
            districts = [current_user.assigned_district]
        else:
            districts = []
        
        # Approved requirements from customers who have access to these districts
        requirements = CustomerRequirement.objects.filter(
            status='approved',
            customer__accessible_districts__in=districts
        ).distinct().order_by('-created_at')
    else:
        requirements = CustomerRequirement.objects.none()

    # Check if AJAX DataTable request
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('draw'):
        draw = int(request.GET.get('draw', 1))
        start = int(request.GET.get('start', 0))
        length = int(request.GET.get('length', 10))
        search_value = request.GET.get('search[value]', '')
        
        # Base count
        records_total = requirements.count()
        
        # Apply search filter
        if search_value:
            from django.db.models import Q
            requirements = requirements.filter(
                Q(title__icontains=search_value) |
                Q(description__icontains=search_value) |
                Q(customer__name__icontains=search_value) |
                Q(category__name__icontains=search_value)
            )
            
        records_filtered = requirements.count()
        
        # Sorting
        order_column_index = int(request.GET.get('order[0][column]', 0))
        order_dir = request.GET.get('order[0][dir]', 'asc')
        
        # Map column index to field
        columns_map = {
            0: 'title',
            1: 'customer__name',
            2: 'category__name',
            3: 'created_at',
            4: 'status',
        }
        sort_field = columns_map.get(order_column_index, 'created_at')
        if order_dir == 'desc':
            sort_field = f'-{sort_field}'
            
        requirements = requirements.order_by(sort_field)
        
        # Pagination
        requirements_slice = requirements[start:start+length]
        
        # Serialize data
        data = []
        for req in requirements_slice:
            items_data = []
            for item in req.items.all():
                items_data.append({
                    'id': item.id,
                    'subcategory_id': item.subcategory_id,
                    'subcategory_name': item.subcategory.name,
                    'count': item.count,
                    'customer_amount': float(item.customer_amount),
                    'admin_markup': float(item.admin_markup),
                    'total_amount': float(item.total_amount),
                    'sold_count': item.get_sold_count,
                    'left_count': item.get_left_count,
                    'description': item.description or '',
                    'image_url': item.image.url if item.image else ''
                })
            
            data.append({
                'id': req.id,
                'title': req.title,
                'description': req.description or '',
                'image_url': req.image.url if req.image else '',
                'customer_name': req.customer.name,
                'customer_email': req.customer.email,
                'customer_id': req.customer.id,
                'category_name': req.category.name if req.category else 'No Category',
                'category_id': req.category_id or '',
                'category_type': req.category.cat_type if req.category else '',
                'created_at_formatted': req.created_at.strftime('%b %d, %Y'),
                'created_at_sort': req.created_at.strftime('%Y%m%d%H%M%S'),
                'status': req.status,
                'items': items_data
            })
            
        return JsonResponse({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data
        })

    categories = Category.objects.all()
    return render(request, 'cyborgapp/requirements/list.html', {
        'requirements': requirements,
        'categories': categories
    })

@login_required(login_url='login')
def requirement_create(request):
    if request.user.usertype != 'customer':
        messages.error(request, 'Only customers can create requirements.')
        return redirect('requirement_list')
        
    if request.method == 'POST':
        category_id = request.POST.get('category')
        subcat_ids = request.POST.getlist('subcategories')
        
        if not category_id or not subcat_ids:
            messages.error(request, 'Category and at least one item are mandatory.')
            return redirect('requirement_list')
            
        requirement = CustomerRequirement.objects.create(
            customer=request.user,
            title=request.POST.get('title'),
            description=request.POST.get('description'),
            category_id=category_id,
            customer_amount=request.POST.get('customer_amount', 0),
            image=request.FILES.get('image')
        )
        
        subcat_ids = request.POST.getlist('subcategories')
        for sub_id in subcat_ids:
            count = request.POST.get(f'count_{sub_id}')
            cust_amt = request.POST.get(f'customer_amount_{sub_id}')
            desc = request.POST.get(f'description_{sub_id}', '')
            
            # Ensure we have valid decimal/int values or default to 0
            clean_count = int(count) if count and count.strip() else 0
            clean_amt = float(cust_amt) if cust_amt and cust_amt.strip() else 0.00
            
            RequirementItem.objects.create(
                requirement=requirement,
                subcategory_id=sub_id,
                count=clean_count,
                customer_amount=clean_amt,
                description=desc.strip(),
                image=request.FILES.get(f'image_{sub_id}')
            )
        
        messages.success(request, 'Requirement created successfully!')
        return redirect('requirement_list')
    return redirect('requirement_list')

@login_required(login_url='login')
def requirement_edit(request, req_id):
    requirement = get_object_or_404(CustomerRequirement, id=req_id)
    
    # NEW RULE: No one can edit after approval
    if requirement.status == 'approved':
        messages.error(request, 'Approved requirements cannot be edited.')
        return redirect('requirement_list')

    # Permission check
    can_edit = False
    if request.user.usertype == 'superadmin':
        can_edit = True
    elif requirement.customer == request.user:
        can_edit = True
            
    if not can_edit:
        messages.error(request, 'Permission denied.')
        return redirect('requirement_list')
        
    if request.method == 'POST':
        if request.user.usertype == 'superadmin':
            # Admin ONLY updates markup and status
            requirement.status = request.POST.get('status', requirement.status)
            
            # Handle "Update and Approve" button
            if request.POST.get('approve_after_update') == 'true':
                requirement.status = 'approved'
                
            requirement.save()
            
            # Update markups for items
            for item in requirement.items.all():
                item.admin_markup = request.POST.get(f'admin_markup_{item.subcategory_id}', 0)
                item.save()
        else:
            # Customer updates their details
            requirement.title = request.POST.get('title')
            requirement.description = request.POST.get('description')
            requirement.category_id = request.POST.get('category')
            requirement.customer_amount = request.POST.get('customer_amount', 0)
            if 'image' in request.FILES:
                requirement.image = request.FILES.get('image')
            requirement.save()
            
            old_items = {item.subcategory_id: item.image for item in requirement.items.all()}
            requirement.items.all().delete()
            subcat_ids = request.POST.getlist('subcategories')
            for sub_id in subcat_ids:
                count = request.POST.get(f'count_{sub_id}')
                cust_amt = request.POST.get(f'customer_amount_{sub_id}')
                desc = request.POST.get(f'description_{sub_id}', '')
                
                clean_count = int(count) if count and count.strip() else 0
                clean_amt = float(cust_amt) if cust_amt and cust_amt.strip() else 0.00
                
                image = request.FILES.get(f'image_{sub_id}')
                if not image and int(sub_id) in old_items:
                    image = old_items[int(sub_id)]
                
                RequirementItem.objects.create(
                    requirement=requirement,
                    subcategory_id=sub_id,
                    count=clean_count,
                    customer_amount=clean_amt,
                    description=desc.strip(),
                    admin_markup=0, # Markup stays 0 until admin sets it
                    image=image
                )
        
        messages.success(request, 'Requirement updated successfully!')
        return redirect('requirement_list')
    return redirect('requirement_list')

from .models import Wallet, CommissionTransaction, WithdrawalRequest
from .utils import get_or_create_wallet
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
import csv
from django.http import HttpResponse, StreamingHttpResponse

class Echo:
    """An object that implements just the write method of the file-like
    interface and returns the string written rather than writing to a file.
    """
    def write(self, value):
        return value

@login_required(login_url='login')
def export_commissions_csv(request):
    current_user = request.user
    
    # Determine the target user
    user_id = request.GET.get('user_id')
    if user_id and current_user.usertype == 'superadmin':
        target_user = get_object_or_404(CustomUser, id=user_id)
    else:
        # Standard users can only export their own wallet commissions
        if current_user.usertype == 'manager' and current_user.assigned_district:
            target_user = current_user.assigned_district
        else:
            target_user = current_user
            
    txs = CommissionTransaction.objects.filter(user=target_user)
    
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    
    if from_date:
        try:
            txs = txs.filter(created_at__date__gte=from_date)
        except Exception:
            pass
    if to_date:
        try:
            txs = txs.filter(created_at__date__lte=to_date)
        except Exception:
            pass
            
    txs = txs.order_by('-created_at')
    
    # Use database iterator with chunk size to keep memory extremely low (0 extra RAM overhead)
    queryset = txs.iterator(chunk_size=2000)
    
    def csv_generator():
        echo_buffer = Echo()
        writer = csv.writer(echo_buffer)
        
        # Yield the header first
        yield writer.writerow(['Date & Time', 'Transaction Type', 'Description', 'Amount (Rs)'])
        
        # Stream the remaining rows from the database chunk-by-chunk
        for tx in queryset:
            yield writer.writerow([
                tx.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                tx.get_transaction_type_display(),
                tx.description,
                float(tx.amount)
            ])
            
    filename = f"commission_history_{target_user.name.lower().replace(' ', '_')}"
    if from_date:
        filename += f"_from_{from_date}"
    if to_date:
        filename += f"_to_{to_date}"
    filename += ".csv"
    
    response = StreamingHttpResponse(csv_generator(), content_type="text/csv")
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

from django.views.decorators.csrf import csrf_exempt
import json

@csrf_exempt
def razorpay_webhook(request):
    if request.method == 'POST':
        import razorpay
        from django.conf import settings
        
        webhook_signature = request.headers.get('X-Razorpay-Signature')
        webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', None)
        
        if not webhook_signature or not webhook_secret:
            return HttpResponse(status=400)
            
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        
        try:
            client.utility.verify_webhook_signature(request.body.decode('utf-8'), webhook_signature, webhook_secret)
        except razorpay.errors.SignatureVerificationError:
            return HttpResponse(status=400)
            
        try:
            payload = json.loads(request.body.decode('utf-8'))
            event = payload.get('event')
            
            if event == 'payment.captured':
                payment_entity = payload['payload']['payment']['entity']
                notes = payment_entity.get('notes', {})
                lead_id = notes.get('lead_id')
                user_id = notes.get('user_id')
                payment_id = payment_entity.get('id')
                
                if lead_id:
                    lead = Lead.objects.filter(id=lead_id).first()
                    if lead and lead.status == 'pending':
                        lead.total_amount = lead.get_total_amount
                        lead.razorpay_payment_id = payment_id
                        from .utils import distribute_product_sale_commission
                        distribute_product_sale_commission(lead)
                        
                        lead.status = 'confirmed'
                        lead.save()
                        
                        LeadUpdate.objects.create(
                            lead=lead, 
                            update_text=f"Payment confirmed via Webhook. Payment ID: {payment_id}"
                        )
                elif user_id:
                    user = CustomUser.objects.filter(id=user_id).first()
                    if user and not user.is_active and user.usertype == 'marketing':
                        user.is_active = True
                        from .utils import handle_registration_commission
                        handle_registration_commission(user)
                        user.save()
                        
            return HttpResponse(status=200)
        except Exception as e:
            return HttpResponse(status=500)
            
    return HttpResponse(status=405)

@login_required(login_url='login')
def wallet_dashboard(request, user_id=None):
    current_user = request.user
    target_user = current_user
    
    if user_id and current_user.usertype == 'superadmin':
        target_user = get_object_or_404(CustomUser, id=user_id)
    elif current_user.usertype == 'manager' and current_user.assigned_district:
        target_user = current_user.assigned_district
        
    wallet = get_or_create_wallet(target_user)
    
    # Check if AJAX DataTable requests
    if request.GET.get('table') == 'commissions' or (request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('table') == 'commissions'):
        draw = int(request.GET.get('draw', 1))
        start = int(request.GET.get('start', 0))
        length = int(request.GET.get('length', 10))
        search_value = request.GET.get('search[value]', '')
        
        txs = CommissionTransaction.objects.filter(user=target_user)
        
        # Date range filtering
        from_date = request.GET.get('from_date')
        to_date = request.GET.get('to_date')
        if from_date:
            try:
                txs = txs.filter(created_at__date__gte=from_date)
            except Exception:
                pass
        if to_date:
            try:
                txs = txs.filter(created_at__date__lte=to_date)
            except Exception:
                pass
                
        records_total = txs.count()
        
        if search_value:
            from django.db.models import Q
            txs = txs.filter(
                Q(transaction_type__icontains=search_value) |
                Q(description__icontains=search_value)
            )
            
        records_filtered = txs.count()
        
        # Sorting
        order_column_index = int(request.GET.get('order[0][column]', 0))
        order_dir = request.GET.get('order[0][dir]', 'asc')
        
        columns_map = {
            0: 'transaction_type',
            1: 'amount',
            2: 'description',
            3: 'created_at',
        }
        sort_field = columns_map.get(order_column_index, 'created_at')
        if order_dir == 'desc':
            sort_field = f'-{sort_field}'
            
        txs = txs.order_by(sort_field)
        txs_slice = txs[start:start+length]
        
        data = []
        for t in txs_slice:
            data.append({
                'type': t.get_transaction_type_display(),
                'amount': float(t.amount),
                'description': t.description,
                'date_formatted': t.created_at.strftime('%b %d, %Y %H:%M')
            })
            
        return JsonResponse({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data
        })

    if request.GET.get('table') == 'requests' or (request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('table') == 'requests'):
        draw = int(request.GET.get('draw', 1))
        start = int(request.GET.get('start', 0))
        length = int(request.GET.get('length', 10))
        search_value = request.GET.get('search[value]', '')
        
        reqs = WithdrawalRequest.objects.filter(user=target_user)
        records_total = reqs.count()
        
        if search_value:
            from django.db.models import Q
            reqs = reqs.filter(
                Q(status__icontains=search_value) |
                Q(account_number__icontains=search_value) |
                Q(account_holder__icontains=search_value)
            )
            
        records_filtered = reqs.count()
        
        # Sorting
        order_column_index = int(request.GET.get('order[0][column]', 0))
        order_dir = request.GET.get('order[0][dir]', 'asc')
        
        columns_map = {
            0: 'amount',
            1: 'status',
            2: 'created_at',
        }
        sort_field = columns_map.get(order_column_index, 'created_at')
        if order_dir == 'desc':
            sort_field = f'-{sort_field}'
            
        reqs = reqs.order_by(sort_field)
        reqs_slice = reqs[start:start+length]
        
        data = []
        for r in reqs_slice:
            data.append({
                'id': r.id,
                'amount': float(r.amount),
                'status': r.status,
                'remarks': r.remarks or '',
                'created_at_formatted': r.created_at.strftime('%b %d, %Y'),
                'account_number': r.account_number or 'N/A',
                'ifsc_code': r.ifsc_code or 'N/A',
                'account_holder': r.account_holder or 'N/A',
            })
            
        return JsonResponse({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data
        })
    
    total_project_gross = 0
    total_commissions_given = 0
    total_customer_payouts = 0
    
    if current_user.usertype == 'superadmin':
        transactions = CommissionTransaction.objects.filter(user=target_user).order_by('-created_at')
        pending_withdrawals = WithdrawalRequest.objects.filter(status='pending').order_by('-created_at')
        all_withdrawal_requests = WithdrawalRequest.objects.all().order_by('-created_at')
        user_pending_count = 0
        
        # Global stats for Superadmin
        total_customer_payouts = CommissionTransaction.objects.filter(transaction_type='sale', user__usertype='customer').aggregate(Sum('amount'))['amount__sum'] or 0
        # Project Gross = Customer Payouts + Total Markup (all commission transactions including admin share)
        total_markup_pool = CommissionTransaction.objects.filter(transaction_type='commission').aggregate(Sum('amount'))['amount__sum'] or 0
        total_project_gross = total_customer_payouts + total_markup_pool
        
        # Commissions Given = Total Markup - Admin's own share
        total_commissions_given = CommissionTransaction.objects.filter(transaction_type='commission').exclude(user__usertype='superadmin').aggregate(Sum('amount'))['amount__sum'] or 0
    else:
        # ...
        transactions = CommissionTransaction.objects.filter(user=target_user).order_by('-created_at')
        pending_withdrawals = WithdrawalRequest.objects.filter(user=target_user, status='pending').order_by('-created_at')
        all_withdrawal_requests = WithdrawalRequest.objects.filter(user=target_user).order_by('-created_at')
        user_pending_count = pending_withdrawals.count()
        
    withdrawal_history = WithdrawalRequest.objects.filter(user=target_user).order_by('-created_at')
    
    return render(request, 'cyborgapp/wallet/dashboard.html', {
        'wallet': wallet,
        'transactions': transactions,
        'pending_withdrawals': pending_withdrawals,
        'user_pending_count': user_pending_count,
        'withdrawal_history': withdrawal_history,
        'all_withdrawal_requests': all_withdrawal_requests,
        'is_viewing_district': target_user != current_user,
        'total_project_gross': total_project_gross,
        'total_commissions_given': total_commissions_given,
        'total_customer_payouts': total_customer_payouts
    })

@login_required(login_url='login')
def export_wallets_csv(request):
    if request.user.usertype != 'superadmin':
        return HttpResponse("Forbidden", status=403)
        
    users = CustomUser.objects.exclude(usertype__in=['superadmin', 'manager']).order_by('name')
    
    # Keeping memory usage low for lakhs of users by streaming and chunking database calls
    queryset = users.iterator(chunk_size=2000)
    
    def csv_generator():
        echo_buffer = Echo()
        writer = csv.writer(echo_buffer)
        
        # Write CSV Header (excluding pending request and action columns, as requested!)
        yield writer.writerow(['Name', 'Email', 'Role', 'Total Earned (Rs)', 'Withdrawn (Rs)', 'Balance (Rs)'])
        
        for u in queryset:
            w = get_or_create_wallet(u)
            yield writer.writerow([
                u.name,
                u.email,
                u.get_usertype_display(),
                float(w.total_earned),
                float(w.withdrawn_amount),
                float(w.balance)
            ])
            
    response = StreamingHttpResponse(csv_generator(), content_type="text/csv")
    response['Content-Disposition'] = 'attachment; filename="user_financial_status.csv"'
    return response

@login_required(login_url='login')
def superadmin_user_wallets(request):
    if request.user.usertype != 'superadmin':
        return redirect('dashboard')
        
    # Check if AJAX DataTable requests
    if request.GET.get('table') == 'requests' or (request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('table') == 'requests'):
        draw = int(request.GET.get('draw', 1))
        start = int(request.GET.get('start', 0))
        length = int(request.GET.get('length', 10))
        search_value = request.GET.get('search[value]', '')
        
        reqs = WithdrawalRequest.objects.all()
        records_total = reqs.count()
        
        if search_value:
            from django.db.models import Q
            reqs = reqs.filter(
                Q(user__name__icontains=search_value) |
                Q(user__usertype__icontains=search_value) |
                Q(status__icontains=search_value) |
                Q(account_number__icontains=search_value) |
                Q(account_holder__icontains=search_value)
            )
            
        records_filtered = reqs.count()
        
        # Sorting
        order_column_index = int(request.GET.get('order[0][column]', 0))
        order_dir = request.GET.get('order[0][dir]', 'asc')
        
        columns_map = {
            0: 'user__name',
            1: 'amount',
            2: 'status',
            3: 'created_at',
        }
        sort_field = columns_map.get(order_column_index, 'created_at')
        if order_dir == 'desc':
            sort_field = f'-{sort_field}'
            
        reqs = reqs.order_by(sort_field)
        reqs_slice = reqs[start:start+length]
        
        data = []
        for r in reqs_slice:
            data.append({
                'id': r.id,
                'user_name': r.user.name,
                'user_usertype': r.user.get_usertype_display(),
                'amount': float(r.amount),
                'status': r.status,
                'remarks': r.remarks or '',
                'created_at_formatted': r.created_at.strftime('%b %d, %Y'),
                'account_number': r.account_number or 'N/A',
                'ifsc_code': r.ifsc_code or 'N/A',
                'account_holder': r.account_holder or 'N/A',
                'phone_linked': r.phone_linked or 'N/A',
            })
            
        return JsonResponse({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data
        })

    if request.GET.get('table') == 'wallets' or (request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('draw')):
        draw = int(request.GET.get('draw', 1))
        start = int(request.GET.get('start', 0))
        length = int(request.GET.get('length', 10))
        search_value = request.GET.get('search[value]', '')
        
        users = CustomUser.objects.exclude(usertype__in=['superadmin', 'manager'])
        records_total = users.count()
        
        if search_value:
            from django.db.models import Q
            users = users.filter(
                Q(name__icontains=search_value) |
                Q(email__icontains=search_value) |
                Q(usertype__icontains=search_value)
            )
            
        records_filtered = users.count()
        
        # Sorting
        order_column_index = int(request.GET.get('order[0][column]', 0))
        order_dir = request.GET.get('order[0][dir]', 'asc')
        
        columns_map = {
            0: 'name',
            1: 'usertype',
            2: 'wallet__total_earned',
            3: 'wallet__withdrawn_amount',
            4: 'wallet__balance',
        }
        sort_field = columns_map.get(order_column_index, 'name')
        if order_dir == 'desc':
            sort_field = f'-{sort_field}'
            
        users = users.order_by(sort_field)
        users_slice = users[start:start+length]
        
        data = []
        for u in users_slice:
            w = get_or_create_wallet(u)
            pending = WithdrawalRequest.objects.filter(user=u, status='pending').aggregate(total=models.Sum('amount'))['total'] or 0
            data.append({
                'id': u.id,
                'name': u.name,
                'email': u.email,
                'usertype_display': u.get_usertype_display(),
                'total_earned': float(w.total_earned),
                'withdrawn_amount': float(w.withdrawn_amount),
                'balance': float(w.balance),
                'pending_amount': float(pending)
            })
            
        return JsonResponse({
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data
        })
        
    users = CustomUser.objects.exclude(usertype__in=['superadmin', 'manager']).order_by('name')
    user_data = []
    
    for user in users:
        wallet = get_or_create_wallet(user)
        pending_amount = WithdrawalRequest.objects.filter(user=user, status='pending').aggregate(total=models.Sum('amount'))['total'] or 0
        user_data.append({
            'user': user,
            'wallet': wallet,
            'pending_amount': pending_amount
        })
        
    all_withdrawal_requests = WithdrawalRequest.objects.all().order_by('-created_at')
    pending_count = WithdrawalRequest.objects.filter(status='pending').count()
    
    return render(request, 'cyborgapp/superadmin/user_wallets.html', {
        'user_data': user_data,
        'all_withdrawal_requests': all_withdrawal_requests,
        'pending_count': pending_count
    })

@login_required(login_url='login')
def get_user_transactions(request, user_id):
    if request.user.usertype != 'superadmin':
        return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        
    user = get_object_or_404(CustomUser, id=user_id)
    transactions = CommissionTransaction.objects.filter(user=user)
    
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    
    if from_date:
        try:
            transactions = transactions.filter(created_at__date__gte=from_date)
        except Exception:
            pass
    if to_date:
        try:
            transactions = transactions.filter(created_at__date__lte=to_date)
        except Exception:
            pass
            
    transactions = transactions.order_by('-created_at')
    
    data = []
    for tx in transactions:
        data.append({
            'type': tx.get_transaction_type_display(),
            'amount': str(tx.amount),
            'description': tx.description,
            'date': tx.created_at.strftime('%b %d, %Y %H:%M')
        })
    return JsonResponse({'status': 'success', 'transactions': data, 'user_name': user.name})

@login_required(login_url='login')
def request_withdrawal(request):
    if request.method == 'POST':
        amount_str = request.POST.get('amount', '0')
        acc_num = request.POST.get('account_number')
        ifsc = request.POST.get('ifsc_code')
        holder = request.POST.get('account_holder')
        phone = request.POST.get('phone_linked')

        try:
            amount = Decimal(amount_str)
        except:
            amount = Decimal('0')
            
        target_user = request.user
        if target_user.usertype == 'manager' and target_user.assigned_district:
            target_user = target_user.assigned_district
            
        wallet = get_or_create_wallet(target_user)
        
        if amount > wallet.balance:
            messages.error(request, 'Insufficient balance.')
        elif amount <= 0:
            messages.error(request, 'Invalid amount.')
        else:
            # Create the withdrawal request with bank details
            WithdrawalRequest.objects.create(
                user=target_user,
                amount=amount,
                account_number=acc_num,
                ifsc_code=ifsc,
                account_holder=holder,
                phone_linked=phone
            )
            
            # Save these as user's latest bank details for pre-filling next time
            target_user.bank_account_number = acc_num
            target_user.bank_ifsc = ifsc
            target_user.bank_account_holder = holder
            target_user.bank_phone = phone
            target_user.save()
            
            messages.success(request, 'Withdrawal request submitted successfully.')
            
    return redirect('wallet_dashboard')

@login_required(login_url='login')
def withdrawal_requests_list(request):
    if request.user.usertype != 'superadmin':
        messages.error(request, 'Permission denied.')
        return redirect('wallet_dashboard')
        
    requests = WithdrawalRequest.objects.all().order_by('-created_at')
    return render(request, 'cyborgapp/wallet/requests.html', {'requests': requests})

@login_required(login_url='login')
def update_withdrawal_status(request, request_id):
    if request.user.usertype != 'superadmin':
        return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        new_status = data.get('status')
        remarks = data.get('remarks', '')
        
        wr = get_object_or_404(WithdrawalRequest, id=request_id)
        if wr.status != 'pending':
             return JsonResponse({'status': 'error', 'message': 'Request already processed'}, status=400)
             
        if new_status == 'approved':
            wallet = get_or_create_wallet(wr.user)
            # Ensure balance is Decimal for comparison
            current_balance = Decimal(str(wallet.balance))
            if current_balance >= wr.amount:
                with transaction.atomic():
                    wallet.balance = current_balance - Decimal(str(wr.amount))
                    wallet.withdrawn_amount = Decimal(str(wallet.withdrawn_amount)) + Decimal(str(wr.amount))
                    wallet.save()
                    wr.status = 'approved'
                    wr.remarks = remarks
                    wr.save()
                return JsonResponse({'status': 'success'})
            else:
                return JsonResponse({'status': 'error', 'message': 'User has insufficient balance now.'}, status=400)
        elif new_status == 'rejected':
            wr.status = 'rejected'
            wr.remarks = remarks
            wr.save()
            return JsonResponse({'status': 'success'})
            
    return JsonResponse({'status': 'error'}, status=400)
        
    return redirect('requirement_list')

@login_required(login_url='login')
def requirement_delete(request, req_id):
    requirement = get_object_or_404(CustomerRequirement, id=req_id)
    
    if requirement.customer != request.user and request.user.usertype != 'superadmin':
        messages.error(request, 'Permission denied.')
        return redirect('requirement_list')

    # Restriction: cannot delete after approval
    if requirement.status == 'approved':
        messages.error(request, 'Approved requirements cannot be deleted.')
        return redirect('requirement_list')
        
    if request.method == 'POST':
        requirement.delete()
        messages.success(request, 'Requirement deleted successfully!')
    return redirect('requirement_list')

@login_required(login_url='login')
def requirement_toggle_status(request, req_id):
    if request.user.usertype != 'superadmin':
        return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        
    if request.method == 'POST':
        requirement = get_object_or_404(CustomerRequirement, id=req_id)
        requirement.status = 'approved' if requirement.status == 'pending' else 'pending'
        requirement.save()
        return JsonResponse({'status': 'success', 'new_status': requirement.status})
    return JsonResponse({'status': 'error'}, status=400)

@login_required(login_url='login')
def lead_create(request, req_id):
    if request.user.usertype != 'marketing':
        messages.error(request, 'Only marketing users can add leads.')
        return redirect('requirement_list')
    
    requirement = get_object_or_404(CustomerRequirement, id=req_id)
    
    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone')
        email = request.POST.get('email')
        address = request.POST.get('address')
        remarks = request.POST.get('remarks')
        
        lead = Lead.objects.create(
            requirement=requirement,
            marketing_user=request.user,
            name=name,
            phone=phone,
            email=email,
            address=address,
            remarks=remarks
        )
        
        # Handle Lead Items
        selected_item_ids = request.POST.getlist('selected_items')
        for sub_id in selected_item_ids:
            count = request.POST.get(f'count_{sub_id}', 0)
            LeadItem.objects.create(
                lead=lead,
                subcategory_id=sub_id,
                count=count if count else 0
            )
            
        messages.success(request, 'Lead added successfully!')
        
        # Smart redirection based on where the user came from
        referer = request.META.get('HTTP_REFERER', '')
        if 'leads' in referer:
            return redirect('lead_list')
        return redirect('requirement_list')
    
    return redirect('requirement_list')

from .models import CustomUser, CustomerRequirement, Lead, LeadItem, LeadUpdate

from django.db.models import Case, When, Value, IntegerField

@login_required(login_url='login')
def lead_list(request):
    user = request.user
    
    # Custom sort: pending first, then by date
    status_order = Case(
        When(status='pending', then=Value(0)),
        When(status='confirmed', then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    
    if user.usertype == 'superadmin':
        leads = Lead.objects.all().order_by(status_order, '-created_at')
    elif user.usertype == 'marketing':
        leads = Lead.objects.filter(marketing_user=user).order_by(status_order, '-created_at')
    elif user.usertype == 'mandalam':
        leads = Lead.objects.filter(marketing_user__assigned_mandalam=user).order_by(status_order, '-created_at')
    elif user.usertype == 'district':
        leads = Lead.objects.filter(marketing_user__assigned_district=user).order_by(status_order, '-created_at')
    elif user.usertype == 'manager':
        if user.assigned_district:
            leads = Lead.objects.filter(marketing_user__assigned_district=user.assigned_district).order_by(status_order, '-created_at')
        else:
            leads = Lead.objects.filter(marketing_user__created_by=user).order_by(status_order, '-created_at')
    else:
        leads = Lead.objects.none()

    # Get approved requirements for marketers to add leads directly from leads section
    approved_requirements = []
    if user.usertype == 'marketing':
        if user.assigned_district:
            districts = [user.assigned_district]
        else:
            districts = []
        approved_requirements = CustomerRequirement.objects.filter(
            status='approved',
            customer__accessible_districts__in=districts
        ).distinct().order_by('-created_at')

    return render(request, 'cyborgapp/leads/list.html', {
        'leads': leads,
        'approved_requirements': approved_requirements
    })

@login_required(login_url='login')
def lead_edit(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    
    # Restriction: confirmed leads cannot be edited
    if lead.status == 'confirmed':
        messages.error(request, 'Confirmed leads cannot be edited.')
        return redirect('lead_list')
    
    # Permission: only current level owner can edit
    can_edit = False
    if request.user.usertype == lead.current_level:
        if request.user.usertype == 'marketing' and lead.marketing_user == request.user:
            can_edit = True
        elif request.user.usertype == 'mandalam' and lead.marketing_user.assigned_mandalam == request.user:
            can_edit = True
        elif request.user.usertype == 'district' and lead.marketing_user.assigned_district == request.user:
            can_edit = True
        elif request.user.usertype == 'manager' and (lead.marketing_user.assigned_district == request.user.assigned_district or lead.marketing_user.created_by == request.user):
            can_edit = True
            
    if not can_edit:
        messages.error(request, 'Permission denied. You do not have control over this lead.')
        return redirect('lead_list')
    
    if request.method == 'POST':
        lead.name = request.POST.get('name')
        lead.phone = request.POST.get('phone')
        lead.email = request.POST.get('email')
        lead.address = request.POST.get('address')
        lead.remarks = request.POST.get('remarks')
        lead.save()
        
        # Update Lead Items
        lead.items.all().delete()
        selected_item_ids = request.POST.getlist('selected_items')
        for sub_id in selected_item_ids:
            count = request.POST.get(f'count_{sub_id}', 0)
            LeadItem.objects.create(
                lead=lead,
                subcategory_id=sub_id,
                count=count if count else 0
            )
            
        messages.success(request, 'Lead updated successfully!')
        return redirect('lead_list')
    return redirect('lead_list')

@login_required(login_url='login')
def lead_add_update(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    
    effective_user_level = request.user.usertype
    if effective_user_level == 'manager':
        effective_user_level = 'district'
        
    if effective_user_level != lead.current_level:
        return JsonResponse({'status': 'error', 'message': 'You do not have control over this lead currently.'}, status=403)
        
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        
        if data.get('payment_failed'):
            LeadUpdate.objects.create(
                lead=lead, 
                update_text="Payment failed not confirmed"
            )
            return JsonResponse({'status': 'success', 'message': 'Payment failure logged.'})

        update_text = data.get('update_text')
        new_status = data.get('status')
        pass_lead = data.get('pass_lead')
        
        if update_text and lead.status == 'pending':
            # Block update if requirement is not approved (only for pending leads)
            if lead.requirement.status != 'approved':
                return JsonResponse({'status': 'error', 'message': 'Requirement for this lead is not in approved state. Currently cannot update, contact admin.'}, status=403)
            
            custom_date = data.get('custom_date')
            custom_time = data.get('custom_time')
            created_at = None
            if custom_date and custom_time:
                try:
                    from django.utils.timezone import make_aware
                    import datetime
                    dt_str = f"{custom_date} {custom_time}:00"
                    naive_dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    created_at = make_aware(naive_dt)
                except Exception:
                    pass
            
            if created_at:
                LeadUpdate.objects.create(
                    lead=lead, 
                    update_text=f"[{request.user.usertype.upper()}] {update_text}",
                    created_at=created_at
                )
            else:
                LeadUpdate.objects.create(
                    lead=lead, 
                    update_text=f"[{request.user.usertype.upper()}] {update_text}"
                )
        
        if new_status in ['pending', 'confirmed'] and lead.status == 'pending':
            # Block status change if requirement is not approved
            if lead.requirement.status != 'approved':
                 return JsonResponse({'status': 'error', 'message': 'Requirement for this lead is not in approved state. Currently cannot update, contact admin.'}, status=403)
            
            old_status = lead.status
            if new_status == 'confirmed' and old_status != 'confirmed':
                # NEW: Check for stock availability if count based
                if lead.requirement.category and lead.requirement.category.cat_type == 'count':
                    for l_item in lead.items.all():
                        req_item = lead.requirement.items.filter(subcategory=l_item.subcategory).first()
                        if req_item and req_item.count: # Only check if max count is set (not infinity)
                             left = req_item.get_left_count
                             requested = l_item.count or 0
                             if requested > left:
                                 return JsonResponse({
                                     'status': 'error', 
                                     'message': f'Insufficient stock for {l_item.subcategory.name}. Available: {left}, Requested in this Lead: {requested}. Please reduce quantity or contact admin.'
                                 }, status=400)

                razorpay_payment_id = data.get('razorpay_payment_id')
                razorpay_order_id = data.get('razorpay_order_id')
                razorpay_signature = data.get('razorpay_signature')

                import razorpay
                from django.conf import settings
                client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

                if not razorpay_payment_id:
                    # Step 1: Create Razorpay Order
                    amount_in_paise = int(lead.get_total_amount * 100)
                    if amount_in_paise > 0:
                        order_data = {
                            'amount': amount_in_paise,
                            'currency': 'INR',
                            'payment_capture': '1',
                            'notes': {
                                'lead_id': str(lead.id),
                                'project': 'onmarketing'
                            }
                        }
                        razorpay_order = client.order.create(data=order_data)
                        return JsonResponse({
                            'status': 'payment_required',
                            'order_id': razorpay_order['id'],
                            'amount': amount_in_paise,
                            'key': settings.RAZORPAY_KEY_ID,
                            'lead_name': lead.name,
                            'lead_email': lead.email,
                            'lead_phone': lead.phone
                        })
                    else:
                        # If amount is 0, proceed directly
                        pass
                else:
                    # Step 2: Verify Payment Signature
                    try:
                        client.utility.verify_payment_signature({
                            'razorpay_order_id': razorpay_order_id,
                            'razorpay_payment_id': razorpay_payment_id,
                            'razorpay_signature': razorpay_signature
                        })
                    except razorpay.errors.SignatureVerificationError:
                        return JsonResponse({'status': 'error', 'message': 'Payment verification failed. Security signature mismatch.'}, status=400)
                    
                    LeadUpdate.objects.create(
                        lead=lead, 
                        update_text=f"Payment verified successfully. Payment ID: {razorpay_payment_id}"
                    )
                    lead.razorpay_payment_id = razorpay_payment_id

                # Save static total amount for history
                lead.total_amount = lead.get_total_amount
                
                from .utils import distribute_product_sale_commission
                distribute_product_sale_commission(lead)
            
            lead.status = new_status
            lead.save()
            
        if pass_lead and lead.status == 'pending':
            # Block passing if requirement is not approved
            if lead.requirement.status != 'approved':
                return JsonResponse({'status': 'error', 'message': 'Requirement for this lead is not in approved state. Currently cannot update, contact admin.'}, status=403)
            
            levels = ['marketing', 'mandalam', 'district', 'superadmin']
            try:
                current_idx = levels.index(lead.current_level)
                if current_idx < len(levels) - 1:
                    old_level = lead.current_level
                    lead.current_level = levels[current_idx + 1]
                    LeadUpdate.objects.create(lead=lead, update_text=f"SYSTEM: Lead passed from {old_level} to {lead.current_level}")
            except ValueError:
                pass
                
        lead.save()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'}, status=400)

@login_required(login_url='login')
def lead_get_updates(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    updates = list(lead.updates.values('update_text', 'created_at').order_by('-created_at'))
    for update in updates:
        update['created_at'] = update['created_at'].strftime('%b %d, %Y %H:%M')
    
    effective_user_level = request.user.usertype
    if effective_user_level == 'manager':
        effective_user_level = 'district'
    
    can_update = (effective_user_level == lead.current_level) and lead.status == 'pending'
    requirement_approved = lead.requirement.status == 'approved'
    
    if not requirement_approved:
        can_update = False
    
    return JsonResponse({
        'status': 'success',
        'updates': updates,
        'lead_status': lead.status,
        'current_level': lead.current_level,
        'can_update': can_update,
        'requirement_approved': requirement_approved
    })

from .models import Category, SubCategory, RequirementItem, LeadItem

@login_required(login_url='login')
def category_list(request):
    if request.user.usertype not in ['superadmin', 'customer']:
        messages.error(request, 'Permission denied.')
        return redirect('superadmin_dashboard')
        
    categories = Category.objects.all().order_by('-created_at')
    for cat in categories:
        cat.is_used = cat.requirements.exists()
        if request.user.usertype == 'customer' and cat.created_by != request.user:
            cat.is_readonly_for_user = True
        else:
            cat.is_readonly_for_user = False
        
    return render(request, 'cyborgapp/categories/list.html', {'categories': categories})

@login_required(login_url='login')
def category_create(request):
    if request.user.usertype not in ['superadmin', 'customer']:
        return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        
    if request.method == 'POST':
        name = request.POST.get('name')
        cat_type = request.POST.get('cat_type')
        subcategories = request.POST.getlist('subcategories')
        
        category = Category.objects.create(name=name, cat_type=cat_type, created_by=request.user)
        for sub_name in subcategories:
            if sub_name.strip():
                SubCategory.objects.create(category=category, name=sub_name.strip(), created_by=request.user)
                
        messages.success(request, 'Category created successfully!')
        return redirect('category_list')
    return redirect('category_list')

@login_required(login_url='login')
def category_edit(request, cat_id):
    if request.user.usertype not in ['superadmin', 'customer']:
        return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        
    category = get_object_or_404(Category, id=cat_id)
    category_is_used = category.requirements.exists()
    is_readonly_for_user = request.user.usertype == 'customer' and category.created_by != request.user
    
    if request.method == 'POST':
        if not category_is_used and not is_readonly_for_user:
            category.name = request.POST.get('name')
            category.cat_type = request.POST.get('cat_type')
            category.save()
            
        submitted_subs = request.POST.getlist('subcategories')
        submitted_subs_clean = [s.strip() for s in submitted_subs if s.strip()]
        
        for existing_sub in category.subcategories.all():
            if existing_sub.name not in submitted_subs_clean:
                sub_is_used = existing_sub.requirementitem_set.exists()
                if not sub_is_used:
                    can_delete = False
                    if request.user.usertype == 'superadmin':
                        can_delete = True
                    elif category.created_by == request.user:
                        can_delete = True
                    elif existing_sub.created_by == request.user:
                        can_delete = True
                        
                    if can_delete:
                        existing_sub.delete()
        
        for sub_name in submitted_subs_clean:
            if not SubCategory.objects.filter(category=category, name=sub_name).exists():
                SubCategory.objects.create(category=category, name=sub_name, created_by=request.user)
                    
        messages.success(request, 'Category updated successfully!')
        return redirect('category_list')
    return redirect('category_list')

@login_required(login_url='login')
def category_delete(request, cat_id):
    if request.user.usertype not in ['superadmin', 'customer']:
        return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        
    category = get_object_or_404(Category, id=cat_id)
    
    if request.user.usertype == 'customer' and category.created_by != request.user:
        messages.error(request, 'Permission denied: Cannot delete a category created by others.')
        return redirect('category_list')
        
    if category.requirements.exists():
        messages.error(request, 'Cannot delete category as it is used in requirements.')
    else:
        category.delete()
        messages.success(request, 'Category deleted successfully!')
    return redirect('category_list')

@login_required(login_url='login')
def get_subcategories(request, cat_id):
    category = get_object_or_404(Category, id=cat_id)
    subcategories = list(category.subcategories.values('id', 'name'))
    return JsonResponse({
        'status': 'success', 
        'cat_type': category.cat_type,
        'subcategories': subcategories
    })

@login_required(login_url='login')
def delete_withdrawal_request(request, request_id):
    withdrawal_request = get_object_or_404(WithdrawalRequest, id=request_id)
    
    target_user = request.user
    if target_user.usertype == 'manager' and target_user.assigned_district:
        target_user = target_user.assigned_district
        
    if withdrawal_request.user != target_user:
        return JsonResponse({'status': 'error', 'message': 'Permission denied: You do not own this request.'}, status=403)
        
    if withdrawal_request.status != 'pending':
        return JsonResponse({'status': 'error', 'message': 'You can only delete requests in the pending stage.'}, status=400)
        
    withdrawal_request.delete()
    return JsonResponse({'status': 'success', 'message': 'Withdrawal request deleted successfully.'})

