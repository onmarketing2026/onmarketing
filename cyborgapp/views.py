from django import forms
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db import IntegrityError, models
from .models import CustomUser, CustomerRequirement, Lead, LeadItem, LeadUpdate, CommissionSetting, Notification, CommissionTransaction, WithdrawalRequest

def create_lead_notification(actor, lead, verb):
    if lead:
        verb = f"[Lead #{lead.id}] {verb}"
    recipients = set()
    
    # 1. Superadmins
    superadmins = CustomUser.objects.filter(usertype='superadmin')
    for sa in superadmins:
        recipients.add(sa)
        
    # 2. Marketing user who owns the lead (if the actor is not that marketing user)
    m_user = lead.marketing_user
    if m_user:
        if m_user != actor:
            recipients.add(m_user)
            
        # 3. District franchise assigned to the marketing user
        if m_user.assigned_district:
            recipients.add(m_user.assigned_district)
            
        # 4. Mandalam franchise (facilitation center) assigned to the marketing user
        if m_user.assigned_mandalam:
            recipients.add(m_user.assigned_mandalam)
            
        # 5. Creator of the marketing user
        if m_user.created_by:
            recipients.add(m_user.created_by)

    # 6. Managers assigned to the same district
    if m_user and m_user.assigned_district:
        managers = CustomUser.objects.filter(usertype='manager', assigned_district=m_user.assigned_district)
        for mgr in managers:
            recipients.add(mgr)

    # Remove the actor themselves from recipients
    recipients.discard(actor)
    
    # Create notification objects
    for recipient in recipients:
        Notification.objects.create(
            recipient=recipient,
            actor=actor,
            verb=verb,
            lead=lead
        )

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
            elif user.usertype == 'staff':
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

def get_target_user(request):
    """
    Returns the target FC user for staff monitoring queries.
    Saves to/reads from session so the monitored FC persists across page loads.
    """
    user = request.user
    if user.is_authenticated and user.usertype == 'staff':
        # If clear_fc is requested, clear the session
        if 'clear_fc' in request.GET:
            request.session.pop('staff_monitored_fc_id', None)
            return user

        # If explicitly passed via GET, update the session
        fc_id = request.GET.get('fc_id')
        if fc_id:
            if user.assigned_facilitation_centers.filter(id=fc_id).exists():
                request.session['staff_monitored_fc_id'] = fc_id

        # Fallback to session
        monitored_fc_id = request.session.get('staff_monitored_fc_id')
        if monitored_fc_id:
            try:
                return user.assigned_facilitation_centers.get(id=monitored_fc_id)
            except Exception:
                pass
    return user

def render_fc_selection(request):
    """Show the FC selection card list for staff on the current nav page."""
    assigned_fcs = request.user.assigned_facilitation_centers.all().order_by('name')
    return render(request, 'cyborgapp/staff/fc_select.html', {
        'assigned_fcs': assigned_fcs,
        'next_url': request.path
    })

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
        
        # Month-specific calculations for Superadmin Graph
        from django.utils import timezone
        today_date = timezone.localtime(timezone.now())
        current_year = today_date.year
        current_month = today_date.month
        
        stats['monthly_sales'] = Lead.objects.filter(
            status='confirmed',
            created_at__year=current_year,
            created_at__month=current_month
        ).aggregate(total=Sum('total_amount'))['total'] or 0
        
        stats['monthly_commissions'] = CommissionTransaction.objects.exclude(
            user__usertype__in=['superadmin', 'customer']
        ).filter(
            created_at__year=current_year,
            created_at__month=current_month
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        stats['monthly_settled'] = WithdrawalRequest.objects.filter(
            status='approved',
            request_type='wallet',
            updated_at__year=current_year,
            updated_at__month=current_month
        ).aggregate(total=Sum('amount'))['total'] or 0
        
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
    elif user.usertype == 'staff':
        stats['assigned_fcs_count'] = user.assigned_facilitation_centers.count()

    return render(request, 'cyborgapp/superadmin/dashboard.html', {'stats': stats})


@login_required(login_url='login')
def superadmin_dashboard_chart_data(request):
    if request.user.usertype != 'superadmin':
        return JsonResponse({'status': 'error', 'message': 'Forbidden'}, status=403)

    import datetime
    from django.utils import timezone

    period = request.GET.get('period', 'month')   # day | month | year
    today  = timezone.localtime(timezone.now())

    try:
        if period == 'day':
            # Accept ?date=YYYY-MM-DD, default to today
            raw_date = request.GET.get('date', today.strftime('%Y-%m-%d'))
            filter_date = datetime.datetime.strptime(raw_date, '%Y-%m-%d').date()

            leads_qs   = Lead.objects.filter(status='confirmed', created_at__date=filter_date)
            comm_qs    = CommissionTransaction.objects.exclude(
                             user__usertype__in=['superadmin', 'customer']
                         ).filter(created_at__date=filter_date)
            settled_qs = WithdrawalRequest.objects.filter(
                             status='approved', request_type='wallet',
                             updated_at__date=filter_date
                         )
            label = filter_date.strftime('%d %b %Y')

        elif period == 'year':
            # Accept ?year=YYYY, default to current year
            filter_year = int(request.GET.get('year', today.year))

            leads_qs   = Lead.objects.filter(status='confirmed', created_at__year=filter_year)
            comm_qs    = CommissionTransaction.objects.exclude(
                             user__usertype__in=['superadmin', 'customer']
                         ).filter(created_at__year=filter_year)
            settled_qs = WithdrawalRequest.objects.filter(
                             status='approved', request_type='wallet',
                             updated_at__year=filter_year
                         )
            label = str(filter_year)

        else:  # month
            # Accept ?month=MM&year=YYYY, default to current month/year
            filter_month = int(request.GET.get('month', today.month))
            filter_year  = int(request.GET.get('year',  today.year))

            leads_qs   = Lead.objects.filter(
                             status='confirmed',
                             created_at__year=filter_year,
                             created_at__month=filter_month
                         )
            comm_qs    = CommissionTransaction.objects.exclude(
                             user__usertype__in=['superadmin', 'customer']
                         ).filter(created_at__year=filter_year, created_at__month=filter_month)
            settled_qs = WithdrawalRequest.objects.filter(
                             status='approved', request_type='wallet',
                             updated_at__year=filter_year,
                             updated_at__month=filter_month
                         )
            month_name = datetime.date(filter_year, filter_month, 1).strftime('%B')
            label = f"{month_name} {filter_year}"

    except (ValueError, TypeError):
        return JsonResponse({'status': 'error', 'message': 'Invalid date parameters.'}, status=400)

    total_sales       = float(leads_qs.aggregate(t=Sum('total_amount'))['t'] or 0)
    total_commissions = float(comm_qs.aggregate(t=Sum('amount'))['t'] or 0)
    total_settled     = float(settled_qs.aggregate(t=Sum('amount'))['t'] or 0)

    return JsonResponse({
        'status': 'success',
        'period': period,
        'label': label,
        'total_sales': total_sales,
        'total_commissions': total_commissions,
        'total_settled': total_settled,
    })


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
    
    if current_user.usertype == 'staff':
        selected_usertype = 'marketing'
        target_user = get_target_user(request)
        if target_user == current_user:
            return render_fc_selection(request)
        users = CustomUser.objects.filter(assigned_mandalam=target_user, usertype='marketing').order_by('-id')
    else:
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
        
        # Map column index to field.
        # Superadmin sees a Password column at index 4, pushing Created By to index 5.
        # All other users don't have that column, so Created By is at index 4.
        if current_user.usertype == 'superadmin':
            columns_map = {
                0: 'name',
                1: 'email',
                2: 'usertype',
                # 3: Hierarchy  (non-orderable)
                # 4: Password   (non-orderable)
                5: 'created_by__name',
                # 6: Status     (non-orderable)
                # 7: Actions    (non-orderable)
            }
        else:
            columns_map = {
                0: 'name',
                1: 'email',
                2: 'usertype',
                # 3: Hierarchy  (non-orderable)
                4: 'created_by__name',
                # 5: Status     (non-orderable)
                # 6: Actions    (non-orderable)
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
            # Hierarchy display fields
            district_name = u.assigned_district.name if u.assigned_district else ''
            mandalam_name = u.assigned_mandalam.name if u.assigned_mandalam else ''
            # For marketing: get the mandalam's district
            mandalam_district_name = ''
            if u.assigned_mandalam and u.assigned_mandalam.assigned_district:
                mandalam_district_name = u.assigned_mandalam.assigned_district.name
            assigned_fcs = ",".join([str(fc.id) for fc in u.assigned_facilitation_centers.all()]) if u.usertype == 'staff' else ''
            assigned_fc_names = [fc.name for fc in u.assigned_facilitation_centers.all()] if u.usertype == 'staff' else []
            data.append({
                'id': u.id,
                'name': u.name,
                'email': u.email,
                'pass_word': u.pass_word or '',
                'usertype': u.usertype,
                'usertype_display': u.get_usertype_display(),
                'created_by': u.created_by.name if u.created_by else 'Superadmin',
                'is_active': u.is_active,
                'assigned_district_id': u.assigned_district_id or '',
                'assigned_district_name': district_name,
                'assigned_mandalam_id': u.assigned_mandalam_id or '',
                'assigned_mandalam_name': mandalam_name,
                'mandalam_district_name': mandalam_district_name,
                'accessible_districts': acc_dists,
                'assigned_facilitation_centers': assigned_fcs,
                'assigned_fc_names': assigned_fc_names
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

        if usertype == 'staff' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Only Superadmin can create Staff.')
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
                        
            # Handle assigned facilitation centers for Staff
            if usertype == 'staff':
                fc_ids = request.POST.getlist('assigned_facilitation_centers')
                if fc_ids:
                    user.assigned_facilitation_centers.set(fc_ids)

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

    if user.usertype == 'staff' and current_user.usertype != 'superadmin':
        messages.error(request, 'Permission denied: Only Superadmin can edit Staff.')
        return redirect('superadmin_users')
        
    if request.method == 'POST':
        new_usertype = request.POST.get('usertype')
        if new_usertype == 'customer' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Cannot assign Associate Company role.')
            return redirect('superadmin_users')
        if new_usertype == 'manager' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Cannot assign Manager role.')
            return redirect('superadmin_users')
        if new_usertype == 'staff' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Cannot assign Staff role.')
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
            
        # Handle assigned facilitation centers for Staff
        if user.usertype == 'staff':
            fc_ids = request.POST.getlist('assigned_facilitation_centers')
            user.assigned_facilitation_centers.set(fc_ids)
                
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
            
        if user.usertype == 'staff' and current_user.usertype != 'superadmin':
            messages.error(request, 'Permission denied: Only Superadmin can delete Staff.')
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

        if user.usertype == 'staff' and current_user.usertype != 'superadmin':
            return JsonResponse({'status': 'error', 'message': 'Permission denied: Only Superadmin can toggle Staff status.'})
        
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
def requirement_detail(request, req_id):
    requirement = get_object_or_404(CustomerRequirement, id=req_id)
    if request.user.usertype == 'staff':
        user = get_target_user(request)
        if user == request.user:
            return redirect('requirement_list')
    else:
        user = request.user
    
    # Check permissions if necessary
    if user.usertype not in ['superadmin', 'marketing', 'district', 'manager', 'mandalam']:
        if requirement.customer != user:
            messages.error(request, 'You do not have permission to view this requirement.')
            return redirect('requirement_list')

    # Mandalam/marketing: verify they have an assignment for this requirement
    if user.usertype in ['mandalam', 'marketing']:
        from .models import RequirementAssignment
        user_mandalam = user if user.usertype == 'mandalam' else user.assigned_mandalam
        if not user_mandalam or not RequirementAssignment.objects.filter(
            requirement_item__requirement=requirement,
            facilitation_center=user_mandalam
        ).exists():
            messages.error(request, 'You are not assigned to this requirement.')
            return redirect('requirement_list')

    # AJAX request for paginated items (card grid)
    if request.GET.get('card_view') == '1':
        from django.db.models import Q
        search = request.GET.get('search', '').strip()
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 12))

        items_qs = requirement.items.select_related('subcategory').all()
        if search:
            items_qs = items_qs.filter(subcategory__name__icontains=search)

        user_mandalam = None
        if user.usertype == 'mandalam':
            user_mandalam = user
        elif user.usertype == 'marketing' and user.assigned_mandalam:
            user_mandalam = user.assigned_mandalam

        if user_mandalam:
            from .models import RequirementAssignment
            assigned_ids = RequirementAssignment.objects.filter(
                requirement_item__requirement=requirement,
                facilitation_center=user_mandalam
            ).values_list('requirement_item_id', flat=True)
            items_qs = items_qs.filter(id__in=assigned_ids)

        total = items_qs.count()
        start = (page - 1) * per_page
        items_slice = items_qs[start:start + per_page]

        from .models import CommissionSetting
        settings_objs = CommissionSetting.objects.all()
        settings = {s.usertype: float(s.percentage) for s in settings_objs}

        data = []
        for item in items_slice:
            my_commission = 0.0
            role = user.usertype
            if role == 'customer':
                my_commission = float(item.customer_amount)
            elif role in ['superadmin', 'marketing', 'mandalam', 'district', 'manager']:
                pct_role = 'district' if role == 'manager' else role
                pct = settings.get(pct_role, 0.0)
                my_commission = float(item.admin_markup) * (pct / 100.0)

            item_data = {
                'id': item.id,
                'subcategory_id': item.subcategory_id,
                'subcategory_name': item.subcategory.name,
                'count': item.count,
                'customer_amount': float(item.customer_amount),
                'admin_markup': float(item.admin_markup),
                'other_expenses': float(item.other_expenses),
                'gst': float(item.gst),
                'total_amount': float(item.total_amount),
                'left_count': item.get_left_count,
                'description': item.description or '',
                'image_url': item.image.url if item.image else '',
                'my_commission': my_commission,
                'mrp': float(item.mrp or 0.00),
                'total_mrp': float(item.total_mrp or 0.00)
            }
            if user_mandalam:
                from .models import RequirementAssignment
                asgn = RequirementAssignment.objects.filter(
                    requirement_item=item, facilitation_center=user_mandalam
                ).first()
                if asgn:
                    item_data['count'] = asgn.assigned_count
                    item_data['left_count'] = asgn.get_left_count
            data.append(item_data)

        return JsonResponse({
            'data': data,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
        })

    items = requirement.items.all()
    return render(request, 'cyborgapp/requirements/detail.html', {
        'requirement': requirement,
        'items': items
    })

@login_required(login_url='login')
def assign_mandalams(request, item_id):
    """District Franchise or Manager assigns a RequirementItem to Facilitation Centers (mandalams).
    
    The limit is based on the associate company's item count (RequirementItem.count).
    Count input is always required regardless of category type.
    """
    if request.user.usertype not in ['district', 'manager']:
        return JsonResponse({'status': 'error', 'message': 'Only District Franchise or Manager can assign requirements to facilitation centers.'}, status=403)

    district_user = request.user
    if request.user.usertype == 'manager':
        if not request.user.assigned_district:
            return JsonResponse({'status': 'error', 'message': 'Manager must be assigned to a district.'}, status=403)
        district_user = request.user.assigned_district

    from .models import RequirementItem, RequirementAssignment, CustomUser
    item = get_object_or_404(RequirementItem, id=item_id)
    cat_type = item.requirement.category.cat_type if item.requirement.category else 'other'
    # Use the associate company's count as the global ceiling (always, regardless of cat_type)
    item_count = item.count or 0

    if request.method == 'POST':
        checked_mandalam_ids = [int(x) for x in request.POST.getlist('mandalams')]

        # Always parse count input for every mandalam regardless of cat_type
        total_new = 0
        assignments_to_save = []
        for m_id in checked_mandalam_ids:
            try:
                cnt = int(request.POST.get(f'count_{m_id}', 0))
                if cnt < 0:
                    cnt = 0
            except (ValueError, TypeError):
                cnt = 0
            assignments_to_save.append((m_id, cnt))
            total_new += cnt

        # Get existing assignments first to check sold count
        existing_assignments = {
            a.facilitation_center_id: a
            for a in RequirementAssignment.objects.filter(
                requirement_item=item,
                facilitation_center__assigned_district=district_user
            ).select_related('facilitation_center')
        }

        # Calculate what has been assigned to other districts
        from django.db.models import Sum
        total_already_assigned = RequirementAssignment.objects.filter(
            requirement_item=item
        ).aggregate(Sum('assigned_count'))['assigned_count__sum'] or 0
        already_this_district_before = sum(a.assigned_count for a in existing_assignments.values())
        other_district_assigned = total_already_assigned - already_this_district_before

        # Validate against available limit for this district
        max_allowed_this_district = max(0, item_count - other_district_assigned) if item_count else None
        if max_allowed_this_district is not None and total_new > max_allowed_this_district:
            return JsonResponse({
                'status': 'error',
                'message': f'Total assigned count ({total_new}) cannot exceed the maximum allowed for this district ({max_allowed_this_district}).'
            }, status=400)

        # 1. Validation for removed/unchecked FCs
        for m_id, assignment in existing_assignments.items():
            if m_id not in checked_mandalam_ids:
                sold = assignment.get_sold_count
                if sold > 0:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Cannot remove Facilitation Center {assignment.facilitation_center.name} because it already has {sold} confirmed lead(s).'
                    }, status=400)

        # 2. Validation for decreased counts on checked FCs
        for m_id, cnt in assignments_to_save:
            if m_id in existing_assignments:
                assignment = existing_assignments[m_id]
                sold = assignment.get_sold_count
                if cnt < sold:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Cannot decrease count for {assignment.facilitation_center.name} to {cnt} because it already has {sold} confirmed lead(s).'
                    }, status=400)

        # Remove FC assignments for this item that are no longer checked (under this district)
        RequirementAssignment.objects.filter(
            requirement_item=item,
            facilitation_center__assigned_district=district_user
        ).exclude(facilitation_center_id__in=checked_mandalam_ids).delete()

        # Save/update selected FC assignments
        for m_id, cnt in assignments_to_save:
            RequirementAssignment.objects.update_or_create(
                requirement_item=item,
                facilitation_center_id=m_id,
                defaults={'assigned_count': cnt, 'assigned_by': request.user}
            )

        return JsonResponse({'status': 'success', 'message': 'Facilitation Center assignments updated successfully.'})

    # GET – return list of FCs under this district with current assignment data
    mandalams = CustomUser.objects.filter(
        usertype='mandalam', assigned_district=district_user, is_active=True
    ).order_by('name')

    current_assignments = {
        a.facilitation_center_id: a.assigned_count
        for a in RequirementAssignment.objects.filter(
            requirement_item=item,
            facilitation_center__assigned_district=district_user
        )
    }

    # How much is already assigned (across all districts' FCs)
    from django.db.models import Sum
    total_already_assigned = RequirementAssignment.objects.filter(
        requirement_item=item
    ).aggregate(Sum('assigned_count'))['assigned_count__sum'] or 0
    # Available for this district to assign = item_count - total_already_assigned
    district_available = max(0, item_count - total_already_assigned) if item_count else None

    mand_list = []
    for mand in mandalams:
        mand_list.append({
            'id': mand.id,
            'name': mand.name or mand.username,
            'checked': mand.id in current_assignments,
            'assigned_count': current_assignments.get(mand.id, 0)
        })

    return JsonResponse({
        'status': 'success',
        'cat_type': cat_type,
        'subcategory_name': item.subcategory.name,
        'item_count': item_count,
        'district_available': district_available,
        'mandalams': mand_list
    })

@login_required(login_url='login')
def requirement_list(request):
    if request.user.usertype == 'staff':
        target_user = get_target_user(request)
        if target_user == request.user:
            return render_fc_selection(request)
    else:
        target_user = request.user

    current_user = target_user
    if current_user.usertype == 'superadmin':
        from django.db.models import Case, When, Value, IntegerField
        requirements = CustomerRequirement.objects.all().annotate(
            is_pending=Case(
                When(status='pending', then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by('is_pending', '-created_at')
    elif current_user.usertype == 'customer':
        requirements = CustomerRequirement.objects.filter(customer=current_user).order_by('-created_at')
    elif current_user.usertype in ['district', 'manager']:
        if current_user.usertype == 'district':
            districts = [current_user]
        elif current_user.assigned_district:
            districts = [current_user.assigned_district]
        else:
            districts = []
        requirements = CustomerRequirement.objects.filter(
            status='approved',
            customer__accessible_districts__in=districts
        ).distinct().order_by('-created_at')
    elif current_user.usertype == 'mandalam':
        from .models import RequirementAssignment
        req_ids = RequirementAssignment.objects.filter(
            facilitation_center=current_user
        ).values_list('requirement_item__requirement_id', flat=True).distinct()
        requirements = CustomerRequirement.objects.filter(
            id__in=req_ids, status='approved'
        ).order_by('-created_at')
    elif current_user.usertype == 'marketing':
        from .models import RequirementAssignment
        mandalam = current_user.assigned_mandalam
        if mandalam:
            req_ids = RequirementAssignment.objects.filter(
                facilitation_center=mandalam
            ).values_list('requirement_item__requirement_id', flat=True).distinct()
            requirements = CustomerRequirement.objects.filter(
                id__in=req_ids, status='approved'
            ).order_by('-created_at')
        else:
            requirements = CustomerRequirement.objects.none()
    else:
        requirements = CustomerRequirement.objects.none()

    # AJAX for card view (district/marketing/manager/mandalam)
    if request.GET.get('card_view') == '1':
        from django.db.models import Q
        search = request.GET.get('search', '').strip()
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 12))

        if search:
            requirements = requirements.filter(
                Q(title__icontains=search) |
                Q(category__name__icontains=search) |
                Q(customer__name__icontains=search)
            )

        total = requirements.count()
        start_idx = (page - 1) * per_page
        reqs_slice = requirements[start_idx:start_idx + per_page]

        data = []
        for req in reqs_slice:
            data.append({
                'id': req.id,
                'title': req.title,
                'category_name': req.category.name if req.category else 'No Category',
                'status': req.status,
                'image_url': req.image.url if req.image else '',
            })
        return JsonResponse({
            'data': data,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
        })

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
            
        if current_user.usertype == 'superadmin':
            requirements = requirements.order_by('is_pending', sort_field)
        else:
            requirements = requirements.order_by(sort_field)
        
        # Pagination
        requirements_slice = requirements[start:start+length]
        
        from .models import CommissionSetting
        settings_objs = CommissionSetting.objects.all()
        settings = {s.usertype: float(s.percentage) for s in settings_objs}

        # Serialize data
        data = []
        for req in requirements_slice:
            items_data = []
            for item in req.items.all():
                my_commission = 0.0
                role = current_user.usertype
                if role == 'customer':
                    my_commission = float(item.customer_amount)
                elif role in ['superadmin', 'marketing', 'mandalam', 'district', 'manager']:
                    pct_role = 'district' if role == 'manager' else role
                    pct = settings.get(pct_role, 0.0)
                    my_commission = float(item.admin_markup) * (pct / 100.0)

                items_data.append({
                    'id': item.id,
                    'subcategory_id': item.subcategory_id,
                    'subcategory_name': item.subcategory.name,
                    'count': item.count,
                    'customer_amount': float(item.customer_amount),
                    'admin_markup': float(item.admin_markup),
                    'other_expenses': float(item.other_expenses),
                    'gst': float(item.gst),
                    'total_amount': float(item.total_amount),
                    'sold_count': item.get_sold_count,
                    'left_count': item.get_left_count,
                    'description': item.description or '',
                    'image_url': item.image.url if item.image else '',
                    'my_commission': my_commission,
                    'mrp': float(item.mrp or 0.00),
                    'total_mrp': float(item.total_mrp or 0.00)
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
            
            # Validate first before saving
            for item in requirement.items.all():
                sub_id = item.subcategory_id
                try:
                    mrp_val = float(request.POST.get(f'mrp_{sub_id}', 0) or 0)
                except (ValueError, TypeError):
                    mrp_val = 0.0
                
                if mrp_val > 0:
                    try:
                        cust_amt = float(item.customer_amount)
                        markup = float(request.POST.get(f'admin_markup_{sub_id}', 0) or 0)
                        expenses = float(request.POST.get(f'other_expenses_{sub_id}', 0) or 0)
                        gst = float(request.POST.get(f'gst_{sub_id}', 0) or 0)
                    except (ValueError, TypeError):
                        continue
                    
                    base = cust_amt + markup + expenses
                    total_amount = base + (base * (gst / 100.0))
                    total_mrp = mrp_val + (mrp_val * (gst / 100.0))
                    if total_mrp < total_amount:
                        messages.error(request, f"Total MRP (incl. GST) for {item.subcategory.name} must be greater than or equal to the total amount (₹{total_amount:.2f}).")
                        return redirect('requirement_list')

            # Update markups, other expenses, GST, and MRP for items
            for item in requirement.items.all():
                sub_id = item.subcategory_id
                try:
                    item.admin_markup = float(request.POST.get(f'admin_markup_{sub_id}', 0) or 0)
                except (ValueError, TypeError):
                    item.admin_markup = 0
                try:
                    item.other_expenses = float(request.POST.get(f'other_expenses_{sub_id}', 0) or 0)
                except (ValueError, TypeError):
                    item.other_expenses = 0
                try:
                    item.gst = float(request.POST.get(f'gst_{sub_id}', 0) or 0)
                except (ValueError, TypeError):
                    item.gst = 0
                try:
                    item.mrp = float(request.POST.get(f'mrp_{sub_id}', 0) or 0)
                except (ValueError, TypeError):
                    item.mrp = 0
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
    
    if request.GET.get('check_empty') == 'true':
        return JsonResponse({'empty': not txs.exists()})
        
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
            
            if event in ['payment.captured', 'payment_link.paid']:
                payment_id = None
                lead_id = None
                user_id = None
                installment_id = None
                
                if event == 'payment.captured':
                    payment_entity = payload['payload']['payment']['entity']
                    notes = payment_entity.get('notes', {})
                    lead_id = notes.get('lead_id')
                    user_id = notes.get('user_id')
                    installment_id = notes.get('installment_id')
                    payment_id = payment_entity.get('id')
                elif event == 'payment_link.paid':
                    payment_link_entity = payload['payload']['payment_link']['entity']
                    notes = payment_link_entity.get('notes', {})
                    lead_id = notes.get('lead_id')
                    installment_id = notes.get('installment_id')
                    payment_entity = payload['payload'].get('payment', {}).get('entity', {})
                    payment_id = payment_entity.get('id')
                
                if lead_id:
                    lead = Lead.objects.filter(id=lead_id).first()
                    if lead:
                        if installment_id:
                            from .models import LeadInstallment
                            installment = LeadInstallment.objects.filter(id=installment_id, lead=lead).first()
                            if installment and installment.status == 'pending':
                                installment.status = 'paid'
                                installment.razorpay_payment_id = payment_id
                                installment.save()
                                
                                LeadUpdate.objects.create(
                                    lead=lead, 
                                    update_text=f"Installment {installment.installment_number} of ₹{installment.amount} paid successfully via Webhook ({event}). Payment ID: {payment_id}"
                                )
                                
                                from .utils import distribute_product_sale_commission
                                distribute_product_sale_commission(lead, installment=installment)
                                
                                # If this is the first installment and lead is still pending, confirm the lead
                                if installment.installment_number == 1 and lead.status == 'pending':
                                    lead.total_amount = lead.get_total_amount
                                    lead.razorpay_payment_id = payment_id
                                    lead.payment_mode = 'part'
                                    lead.status = 'confirmed'
                                    lead.save()
                        else:
                            if lead.status == 'pending':
                                lead.total_amount = lead.get_total_amount
                                lead.razorpay_payment_id = payment_id
                                from .utils import distribute_product_sale_commission
                                distribute_product_sale_commission(lead)
                                
                                lead.status = 'confirmed'
                                lead.save()
                                
                                LeadUpdate.objects.create(
                                    lead=lead, 
                                    update_text=f"Payment confirmed via Webhook ({event}). Payment ID: {payment_id}"
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
    
    if current_user.usertype == 'staff':
        target_user = get_target_user(request)
        if target_user == current_user:
            return render_fc_selection(request)
    elif user_id and current_user.usertype == 'superadmin':
        target_user = get_object_or_404(CustomUser, id=user_id)
    elif current_user.usertype == 'manager' and current_user.assigned_district:
        target_user = current_user.assigned_district
    else:
        target_user = current_user
        
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
            desc = t.description
            if t.transaction_type in ['sale', 'commission'] and t.reference_id:
                desc = f"[Lead #{t.reference_id}] {desc}"
            data.append({
                'type': t.get_transaction_type_display(),
                'amount': float(t.amount),
                'description': desc,
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
        
    users = CustomUser.objects.exclude(usertype__in=['superadmin', 'manager'])
    
    # Apply date filtration
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    if from_date:
        try:
            users = users.filter(date_joined__date__gte=from_date)
        except Exception:
            pass
    if to_date:
        try:
            users = users.filter(date_joined__date__lte=to_date)
        except Exception:
            pass
            
    users = users.order_by('name')
    
    if request.GET.get('check_empty') == 'true':
        return JsonResponse({'empty': not users.exists()})
        
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
        
        users = CustomUser.objects.exclude(usertype__in=['superadmin', 'manager', 'staff'])
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
        
    users = CustomUser.objects.exclude(usertype__in=['superadmin', 'manager', 'staff']).order_by('name')
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
        desc = tx.description
        if tx.transaction_type in ['sale', 'commission'] and tx.reference_id:
            desc = f"[Lead #{tx.reference_id}] {desc}"
        data.append({
            'type': tx.get_transaction_type_display(),
            'amount': str(tx.amount),
            'description': desc,
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
            if wr.request_type in ['gst', 'expense']:
                from .models import Lead
                if wr.request_type == 'gst':
                    total_earned_gst = sum(lead.get_gst_amount for lead in Lead.objects.filter(status='confirmed'))
                    withdrawn_gst = sum(r.amount for r in WithdrawalRequest.objects.filter(request_type='gst', status='approved'))
                    current_balance = total_earned_gst - withdrawn_gst
                else:
                    total_earned_expense = sum(lead.get_expense_amount for lead in Lead.objects.filter(status='confirmed'))
                    withdrawn_expense = sum(r.amount for r in WithdrawalRequest.objects.filter(request_type='expense', status='approved'))
                    current_balance = total_earned_expense - withdrawn_expense
                
                if current_balance >= wr.amount:
                    wr.status = 'approved'
                    wr.remarks = remarks
                    wr.save()
                    return JsonResponse({'status': 'success'})
                else:
                    return JsonResponse({'status': 'error', 'message': 'Insufficient balance in GST/Expense account.'}, status=400)
            else:
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
        selected_item_ids = request.POST.getlist('selected_items')

        # Validate count limits from POST data
        from .models import RequirementAssignment
        m_user = request.user
        
        for sub_id in selected_item_ids:
            req_item = requirement.items.filter(subcategory_id=sub_id).first()
            if not req_item:
                messages.error(request, 'Invalid requirement item selection.')
                return redirect(request.META.get('HTTP_REFERER', 'lead_list'))
            
            # Determine the requested count (1 for non-count categories, or from POST for count categories)
            if requirement.category and requirement.category.cat_type == 'count':
                try:
                    count_val = int(request.POST.get(f'count_{sub_id}', 0))
                except (ValueError, TypeError):
                    count_val = 0
            else:
                count_val = 1
                
            # If it's a count category, check global stock
            if requirement.category and requirement.category.cat_type == 'count':
                left_global = req_item.get_left_count
                if count_val > left_global:
                    messages.error(request, f'Insufficient global stock for {req_item.subcategory.name}. Available: {left_global}, Requested: {count_val}.')
                    return redirect(request.META.get('HTTP_REFERER', 'lead_list'))
            
            # Check facilitation center limits
            if m_user.assigned_mandalam:
                asgn = RequirementAssignment.objects.filter(
                    requirement_item=req_item,
                    facilitation_center=m_user.assigned_mandalam
                ).first()
                
                # If they are not assigned to this subcategory, block them
                if not asgn:
                    messages.error(request, f'Your facilitation center ({m_user.assigned_mandalam.name}) is not assigned to {req_item.subcategory.name}.')
                    return redirect(request.META.get('HTTP_REFERER', 'lead_list'))
                
                # If they have a limit (either count category OR assigned_count > 0), check left count
                if (requirement.category and requirement.category.cat_type == 'count') or asgn.assigned_count > 0:
                    left_mandalam = asgn.get_left_count
                    if count_val > left_mandalam:
                        messages.error(request, f'Insufficient assigned count for {req_item.subcategory.name} in your facilitation center. Available: {left_mandalam}, Requested: {count_val}.')
                        return redirect(request.META.get('HTTP_REFERER', 'lead_list'))
            else:
                messages.error(request, 'You must be assigned to a facilitation center to create leads.')
                return redirect(request.META.get('HTTP_REFERER', 'lead_list'))
        
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
        
        # Trigger Notification
        create_lead_notification(request.user, lead, f"added a new lead '{lead.name}' for '{requirement.title}'")
        
        # Smart redirection based on where the user came from
        referer = request.META.get('HTTP_REFERER', '')
        if 'leads' in referer:
            return redirect('lead_list')
        elif 'detail' in referer:
            return redirect('requirement_detail', req_id=req_id)
        return redirect('requirement_list')
    
    return redirect('requirement_list')

from .models import CustomUser, CustomerRequirement, Lead, LeadItem, LeadUpdate

from django.db.models import Case, When, Value, IntegerField

@login_required(login_url='login')
def lead_list(request):
    if request.user.usertype == 'staff':
        target_user = get_target_user(request)
        if target_user == request.user:
            return render_fc_selection(request)
        user = target_user
    else:
        user = request.user
    from .models import LeadInstallment
    from django.db.models import Exists, OuterRef

    # Annotate each lead with whether it has any pending installments
    pending_inst_sq = LeadInstallment.objects.filter(lead=OuterRef('pk'), status='pending')

    if user.usertype == 'superadmin':
        control_cond = Q(status='pending', current_level='superadmin')
    elif user.usertype == 'marketing':
        control_cond = Q(status='pending', current_level='marketing', marketing_user=user)
    elif user.usertype == 'mandalam':
        control_cond = Q(status='pending', current_level='mandalam', marketing_user__assigned_mandalam=user)
    elif user.usertype == 'district':
        control_cond = Q(status='pending', current_level='district', marketing_user__assigned_district=user)
    elif user.usertype == 'manager':
        if user.assigned_district:
            control_cond = Q(status='pending', current_level='manager', marketing_user__assigned_district=user.assigned_district)
        else:
            control_cond = Q(status='pending', current_level='manager', marketing_user__created_by=user)
    else:
        control_cond = Q(pk__in=[])

    def annotated_leads(qs):
        return qs.annotate(
            installment_pending=Exists(pending_inst_sq)
        ).exclude(
            status='confirmed',
            installment_pending=False
        ).annotate(
            is_controlled=Case(
                When(control_cond, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            ),
            display_order=Case(
                When(status='pending', then=Value(0)),
                When(status='confirmed', payment_mode='part', installment_pending=True, then=Value(1)),
                When(status='confirmed', then=Value(2)),
                default=Value(2),
                output_field=IntegerField()
            )
        ).order_by('-is_controlled', 'display_order', '-created_at')

    if user.usertype == 'superadmin':
        leads = annotated_leads(Lead.objects.all())
    elif user.usertype == 'marketing':
        leads = annotated_leads(Lead.objects.filter(marketing_user=user))
    elif user.usertype == 'mandalam':
        leads = annotated_leads(Lead.objects.filter(marketing_user__assigned_mandalam=user))
    elif user.usertype == 'district':
        leads = annotated_leads(Lead.objects.filter(marketing_user__assigned_district=user))
    elif user.usertype == 'manager':
        if user.assigned_district:
            leads = annotated_leads(Lead.objects.filter(marketing_user__assigned_district=user.assigned_district))
        else:
            leads = annotated_leads(Lead.objects.filter(marketing_user__created_by=user))
    else:
        leads = Lead.objects.none()

    # Get approved requirements for marketers to add leads directly from leads section
    approved_requirements = []
    req_assigned_items_json = {}   # req.id -> list of item dicts (filtered by FC assignment)
    if user.usertype == 'marketing':
        user_mandalam = user.assigned_mandalam
        from .models import RequirementAssignment
        if user_mandalam:
            req_ids = RequirementAssignment.objects.filter(
                facilitation_center=user_mandalam
            ).values_list('requirement_item__requirement_id', flat=True).distinct()
            approved_requirements = CustomerRequirement.objects.filter(
                id__in=req_ids, status='approved'
            ).order_by('-created_at')
        else:
            approved_requirements = CustomerRequirement.objects.none()

        # Build per-requirement item data filtered to only FC-assigned items
        from .models import RequirementAssignment
        for req in approved_requirements:
            if user_mandalam:
                assignments = RequirementAssignment.objects.filter(
                    requirement_item__requirement=req,
                    facilitation_center=user_mandalam
                ).select_related('requirement_item__subcategory')
                req_assigned_items_json[str(req.id)] = [
                    {
                        'id': a.requirement_item.subcategory_id,
                        'name': a.requirement_item.subcategory.name,
                        'count': a.assigned_count,
                        'price': float(a.requirement_item.total_amount),
                        'left': a.get_left_count,
                    }
                    for a in assignments
                ]
            else:
                items = list(req.items.select_related('subcategory').all())
                req_assigned_items_json[str(req.id)] = [
                    {
                        'id': item.subcategory_id,
                        'name': item.subcategory.name,
                        'count': item.count,
                        'price': float(item.total_amount),
                        'left': item.get_left_count,
                    }
                    for item in items
                ]

    import json
    return render(request, 'cyborgapp/leads/list.html', {
        'leads': leads,
        'approved_requirements': approved_requirements,
        'req_assigned_items_json': json.dumps(req_assigned_items_json),
    })

@login_required(login_url='login')
def confirmed_lead_list(request):
    if request.user.usertype == 'staff':
        target_user = get_target_user(request)
        if target_user == request.user:
            return render_fc_selection(request)
        user = target_user
    else:
        user = request.user
        
    from .models import LeadInstallment, Lead
    from django.db.models import Exists, OuterRef, Case, When, Value, IntegerField, Q

    pending_inst_sq = LeadInstallment.objects.filter(lead=OuterRef('pk'), status='pending')

    def annotated_leads(qs):
        return qs.annotate(
            installment_pending=Exists(pending_inst_sq)
        ).annotate(
            display_order=Case(
                When(status='pending', then=Value(0)),
                When(status='confirmed', payment_mode='part', installment_pending=True, then=Value(1)),
                When(status='confirmed', then=Value(2)),
                default=Value(2),
                output_field=IntegerField()
            )
        ).order_by('display_order', '-created_at')

    # Filter only status='confirmed' leads, requiring at least first installment paid if it is a part payment
    base_qs = Lead.objects.filter(status='confirmed').filter(
        Q(payment_mode='single') | 
        Q(payment_mode='part', installments__installment_number=1, installments__status='paid')
    ).distinct()

    if user.usertype == 'superadmin':
        leads = annotated_leads(base_qs)
    elif user.usertype == 'marketing':
        leads = annotated_leads(base_qs.filter(marketing_user=user))
    elif user.usertype == 'mandalam':
        leads = annotated_leads(base_qs.filter(marketing_user__assigned_mandalam=user))
    elif user.usertype == 'district':
        leads = annotated_leads(base_qs.filter(marketing_user__assigned_district=user))
    elif user.usertype == 'manager':
        if user.assigned_district:
            leads = annotated_leads(base_qs.filter(marketing_user__assigned_district=user.assigned_district))
        else:
            leads = annotated_leads(base_qs.filter(marketing_user__created_by=user))
    elif user.usertype == 'customer':
        leads = annotated_leads(base_qs.filter(requirement__customer=user))
    else:
        leads = Lead.objects.none()

    import json
    return render(request, 'cyborgapp/leads/list.html', {
        'leads': leads,
        'approved_requirements': [],
        'req_assigned_items_json': json.dumps({}),
        'is_confirmed_tab': True,
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
        
        # Validate count limits from POST data
        from .models import RequirementAssignment
        m_user = lead.marketing_user
        requirement = lead.requirement
        selected_item_ids = request.POST.getlist('selected_items')
        
        for sub_id in selected_item_ids:
            req_item = requirement.items.filter(subcategory_id=sub_id).first()
            if not req_item:
                messages.error(request, 'Invalid requirement item selection.')
                return redirect('lead_list')
            
            # Determine the requested count (1 for non-count categories, or from POST for count categories)
            if requirement.category and requirement.category.cat_type == 'count':
                try:
                    count_val = int(request.POST.get(f'count_{sub_id}', 0))
                except (ValueError, TypeError):
                    count_val = 0
            else:
                count_val = 1
                
            # If it's a count category, check global stock
            if requirement.category and requirement.category.cat_type == 'count':
                left_global = req_item.get_left_count
                if count_val > left_global:
                    messages.error(request, f'Insufficient global stock for {req_item.subcategory.name}. Available: {left_global}, Requested: {count_val}.')
                    return redirect('lead_list')
            
            # Check facilitation center limits
            if m_user.assigned_mandalam:
                asgn = RequirementAssignment.objects.filter(
                    requirement_item=req_item,
                    facilitation_center=m_user.assigned_mandalam
                ).first()
                
                # If they are not assigned to this subcategory, block them
                if not asgn:
                    messages.error(request, f'Facilitation center ({m_user.assigned_mandalam.name}) is not assigned to {req_item.subcategory.name}.')
                    return redirect('lead_list')
                
                # If they have a limit (either count category OR assigned_count > 0), check left count
                if (requirement.category and requirement.category.cat_type == 'count') or asgn.assigned_count > 0:
                    left_mandalam = asgn.get_left_count
                    if count_val > left_mandalam:
                        messages.error(request, f'Insufficient assigned count for {req_item.subcategory.name} in the facilitation center. Available: {left_mandalam}, Requested: {count_val}.')
                        return redirect('lead_list')
            else:
                messages.error(request, 'Marketing user must be assigned to a facilitation center.')
                return redirect('lead_list')

        # Update Lead Items
        lead.items.all().delete()
        for sub_id in selected_item_ids:
            count = request.POST.get(f'count_{sub_id}', 0)
            LeadItem.objects.create(
                lead=lead,
                subcategory_id=sub_id,
                count=count if count else 0
            )
            
        messages.success(request, 'Lead updated successfully!')
        
        # Trigger Notification
        create_lead_notification(request.user, lead, f"updated lead '{lead.name}' details")
        
        return redirect('lead_list')
    return redirect('lead_list')

def check_assignment_limits(lead):
    from .models import RequirementAssignment
    m_user = lead.marketing_user
    requirement = lead.requirement
    cat_type = requirement.category.cat_type if requirement.category else 'other'

    for l_item in lead.items.all():
        req_item = requirement.items.filter(subcategory=l_item.subcategory).first()
        if not req_item:
            return f"Subcategory {l_item.subcategory.name} not found in this requirement."

        # Determine how many units this lead item consumes
        if cat_type == 'count':
            requested = l_item.count or 0
            # Check global stock
            left_global = req_item.get_left_count
            if requested > left_global:
                return f"Insufficient global stock for {l_item.subcategory.name}. Available: {left_global}, Requested: {requested}."
        else:
            requested = 1  # non-count: each lead item consumes 1 slot

        # Check facilitation center assignment
        if not m_user.assigned_mandalam:
            return "Marketing user must be assigned to a facilitation center to confirm this lead."

        asgn = RequirementAssignment.objects.filter(
            requirement_item=req_item,
            facilitation_center=m_user.assigned_mandalam
        ).first()

        if not asgn:
            return f"Your facilitation center ({m_user.assigned_mandalam.name}) is not assigned to {l_item.subcategory.name}."

        # For count-type OR any assignment that has a numeric limit, check left count
        if cat_type == 'count' or asgn.assigned_count > 0:
            left_mandalam = asgn.get_left_count
            if requested > left_mandalam:
                return (
                    f"Insufficient assigned count for {l_item.subcategory.name} in your facilitation center "
                    f"({m_user.assigned_mandalam.name}). Available: {left_mandalam}, Requested: {requested}."
                )

    return None


@login_required(login_url='login')
def share_lead_payment(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    
    # Check count assignment limits (only for pending leads)
    if lead.status == 'pending':
        limit_error = check_assignment_limits(lead)
        if limit_error:
            return JsonResponse({'status': 'error', 'message': limit_error}, status=400)
    
    # 1. Validation: Only the user currently controlling the lead is allowed to share
    effective_user_level = request.user.usertype
    if effective_user_level == 'manager':
        effective_user_level = 'district'
        
    if effective_user_level != lead.current_level:
        return JsonResponse({'status': 'error', 'message': 'You do not have control over this lead currently.'}, status=403)
        
    if not lead.email:
        return JsonResponse({'status': 'error', 'message': 'Lead email is required to share details and payment link.'}, status=400)
    
    total_amount = lead.get_total_amount
    amount_in_paise = int(total_amount * 100)
    if amount_in_paise <= 0:
        return JsonResponse({'status': 'error', 'message': 'Lead total amount must be greater than 0 to generate a payment link.'}, status=400)

    try:
        import razorpay
        import time
        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string
        
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        
        # 2. Sanitization: Clean phone number and ensure it conforms to Razorpay constraints (8 to 14 chars)
        phone = lead.phone.strip() if lead.phone else ""
        if phone:
            phone = "".join(filter(str.isdigit, phone))
            if len(phone) == 10:
                phone = f"+91{phone}"
            elif len(phone) > 10 and not phone.startswith('+'):
                phone = f"+{phone}"
            
            # Safely omit invalid phone lengths to prevent Razorpay validation exceptions
            if not (8 <= len(phone) <= 14):
                phone = ""

        # Create Razorpay Payment Link(s)
        short_url = None
        rich_installments = []
        
        if lead.status == 'confirmed' and lead.payment_mode == 'part':
            next_inst = lead.installments.filter(status='pending').order_by('installment_number').first()
            if not next_inst:
                return JsonResponse({'status': 'error', 'message': 'All installments for this lead have already been paid.'}, status=400)
            
            payment_link = ""
            inst_amount_in_paise = int(next_inst.amount * 100)
            inst_desc = f"Payment for Installment #{next_inst.installment_number} of Lead #{lead.id}"
            if len(inst_desc) > 200:
                inst_desc = inst_desc[:197] + "..."
                
            inst_link_data = {
                "amount": inst_amount_in_paise,
                "currency": "INR",
                "accept_partial": False,
                "description": inst_desc,
                "customer": {
                    "name": lead.name,
                    "email": lead.email,
                },
                "notify": {
                    "sms": False,
                    "email": False
                },
                "notes": {
                    "lead_id": str(lead.id),
                    "installment_id": str(next_inst.id)
                }
            }
            if phone:
                inst_link_data["customer"]["contact"] = phone
                
            try:
                inst_payment_link = client.payment_link.create(inst_link_data)
                payment_link = inst_payment_link.get('short_url')
            except Exception as rzp_err:
                print(f"Error creating installment payment link: {rzp_err}")
                payment_link = ""
            
            rich_installments.append({
                'installment_number': next_inst.installment_number,
                'amount': next_inst.amount,
                'status': next_inst.status,
                'payment_link': payment_link,
                'razorpay_payment_id': next_inst.razorpay_payment_id or ''
            })
        else:
            items_desc = ", ".join([f"{item.subcategory.name} (Qty: {item.count})" for item in lead.items.all()])
            desc = f"Payment for Lead #{lead.id}: {items_desc}"
            if len(desc) > 200:
                desc = desc[:197] + "..."

            link_data = {
                "amount": amount_in_paise,
                "currency": "INR",
                "accept_partial": False,
                "description": desc,
                "customer": {
                    "name": lead.name,
                    "email": lead.email,
                },
                "notify": {
                    "sms": False,
                    "email": False
                },
                "notes": {
                    "lead_id": str(lead.id)
                }
            }
            if phone:
                link_data["customer"]["contact"] = phone

            payment_link = client.payment_link.create(link_data)
            short_url = payment_link.get('short_url')
        
        # Construct rich subcategory items for the email
        rich_items = []
        for item in lead.items.all():
            req_item = lead.requirement.items.filter(subcategory=item.subcategory).first()
            image_url = None
            if req_item and req_item.image:
                image_url = request.build_absolute_uri(req_item.image.url)
            
            rich_items.append({
                'name': item.subcategory.name,
                'count': item.count,
                'price': float(req_item.total_amount) if req_item else 0.00,
                'total_mrp': float(req_item.total_mrp) if req_item else 0.00,
                'description': req_item.description if req_item else "",
                'image_url': image_url
            })

        # Send Email
        subject = f"Product Requirements & Payment Details - Lead #{lead.id}"
        from_email = settings.DEFAULT_FROM_EMAIL
        to_email = [lead.email]
        
        html_content = render_to_string('cyborgapp/emails/lead_payment_share.html', {
            'lead': lead,
            'payment_link': short_url,
            'rich_items': rich_items,
            'rich_installments': rich_installments
        })
        
        msg = EmailMultiAlternatives(subject, "", from_email, to_email)
        msg.attach_alternative(html_content, "text/html")
        
        try:
            sent_count = msg.send(fail_silently=False)
            if sent_count == 0:
                # Brevo accepted the connection but refused to send - typically a sender verification issue
                return JsonResponse({
                    'status': 'error',
                    'message': f'Email was not sent (0 emails delivered). Please verify that "{from_email}" is a verified sender in your Brevo account.'
                }, status=500)
        except Exception as email_error:
            import traceback
            print("EMAIL SEND ERROR:", traceback.format_exc())
            return JsonResponse({
                'status': 'error',
                'message': f'Email sending failed: {str(email_error)}'
            }, status=500)
        
        # Create Lead Update for progress log
        if lead.payment_mode == 'part':
            update_msg = f"Shared details and installment payment links to {lead.email}."
        else:
            update_msg = f"Shared details and payment link to {lead.email}. Link: {short_url}"
            
        LeadUpdate.objects.create(
            lead=lead,
            update_text=update_msg
        )
        
        return JsonResponse({'status': 'success', 'message': 'Details and payment link shared successfully via email!'})
    except Exception as e:
        import traceback
        print("SHARE LEAD ERROR:", traceback.format_exc())
        return JsonResponse({'status': 'error', 'message': f'Failed to share: {str(e)}'}, status=500)

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

        # Escalation and Confirmed status are mutually exclusive
        if pass_lead and new_status == 'confirmed':
            return JsonResponse({
                'status': 'error',
                'message': 'You cannot escalate to the next level and confirm the lead at the same time. Please choose one.'
            }, status=400)
        

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
            
            usertype_display = request.user.get_usertype_display().upper()
            if created_at:
                LeadUpdate.objects.create(
                    lead=lead, 
                    update_text=f"[{usertype_display}] {update_text}",
                    created_at=created_at
                )
            else:
                LeadUpdate.objects.create(
                    lead=lead, 
                    update_text=f"[{usertype_display}] {update_text}"
                )
        
        if new_status in ['pending', 'confirmed'] and lead.status == 'pending':
            # Block status change if requirement is not approved
            if lead.requirement.status != 'approved':
                 return JsonResponse({'status': 'error', 'message': 'Requirement for this lead is not in approved state. Currently cannot update, contact admin.'}, status=403)
            
            old_status = lead.status
            if new_status == 'confirmed' and old_status != 'confirmed':
                # Check count assignment and stock limits
                limit_error = check_assignment_limits(lead)
                if limit_error:
                    return JsonResponse({'status': 'error', 'message': limit_error}, status=400)

                razorpay_payment_id = data.get('razorpay_payment_id')
                razorpay_order_id = data.get('razorpay_order_id')
                razorpay_signature = data.get('razorpay_signature')

                import razorpay
                from django.conf import settings
                from decimal import Decimal
                from .models import LeadInstallment
                client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

                user_can_split = request.user.usertype in ['superadmin', 'district', 'manager']
                payment_mode = data.get('payment_mode', 'single') if user_can_split else 'single'

                if payment_mode == 'part':
                    installments_list = data.get('installments', [])
                    if len(installments_list) < 2:
                        return JsonResponse({'status': 'error', 'message': 'Part payment must have at least 2 parts.'}, status=400)
                    
                    try:
                        parsed_installments = [Decimal(str(x)) for x in installments_list]
                        if any(x <= 0 for x in parsed_installments):
                            return JsonResponse({'status': 'error', 'message': 'All installment amounts must be positive.'}, status=400)
                    except (ValueError, TypeError):
                        return JsonResponse({'status': 'error', 'message': 'Invalid installment amounts.'}, status=400)

                    total_inst_amount = sum(parsed_installments)
                    total_lead_amount = lead.get_total_amount
                    if abs(total_inst_amount - total_lead_amount) > Decimal('0.01'):
                        return JsonResponse({'status': 'error', 'message': f'The sum of installment amounts (₹{total_inst_amount}) must equal the total lead amount (₹{total_lead_amount}).'}, status=400)

                    # Extract and validate installment dates
                    installment_dates = data.get('installment_dates', [])
                    if len(installment_dates) != len(installments_list):
                        return JsonResponse({'status': 'error', 'message': 'Installment dates are mandatory for every installment.'}, status=400)
                    
                    import datetime
                    parsed_dates = []
                    for idx, dt_str in enumerate(installment_dates):
                        if not dt_str:
                            return JsonResponse({'status': 'error', 'message': f'Due date is mandatory for installment {idx+1}.'}, status=400)
                        try:
                            dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d").date()
                            parsed_dates.append(dt)
                        except ValueError:
                            return JsonResponse({'status': 'error', 'message': f'Invalid date format for installment {idx+1}.'}, status=400)
                    
                    for i in range(1, len(parsed_dates)):
                        if parsed_dates[i] < parsed_dates[i-1]:
                            return JsonResponse({'status': 'error', 'message': f'Installment {i+1} due date cannot be earlier than installment {i} due date.'}, status=400)

                    from django.utils import timezone
                    today = timezone.localtime(timezone.now()).date()
                    if parsed_dates[0] != today:
                        return JsonResponse({'status': 'error', 'message': f"First installment date must be today's date ({today})."}, status=400)

                    share_payment_link = data.get('share_payment_link', False)
                    if share_payment_link:
                        # 1. Update Lead models & Installment objects
                        lead.payment_mode = 'part'
                        lead.total_amount = lead.get_total_amount
                        lead.confirmed_by = request.user
                        lead.status = new_status
                        lead.save()
                        
                        # Delete existing ones just in case we are retrying / re-splitting
                        lead.installments.all().delete()
                        for idx, amt in enumerate(parsed_installments, start=1):
                            LeadInstallment.objects.create(
                                lead=lead,
                                installment_number=idx,
                                amount=amt,
                                status='pending',
                                due_date=parsed_dates[idx-1]
                            )
                            
                        # 2. Generate and share payment link for the first installment via email
                        if not lead.email:
                            return JsonResponse({'status': 'error', 'message': 'Lead email is required to share details and payment link.'}, status=400)
                        
                        first_inst = lead.installments.first()
                        if not first_inst:
                            return JsonResponse({'status': 'error', 'message': 'No installments created.'}, status=400)
                            
                        phone = lead.phone.strip() if lead.phone else ""
                        if phone:
                            phone = "".join(filter(str.isdigit, phone))
                            if len(phone) == 10:
                                phone = f"+91{phone}"
                            elif len(phone) > 10 and not phone.startswith('+'):
                                phone = f"+{phone}"
                            if not (8 <= len(phone) <= 14):
                                phone = ""
                                
                        inst_amount_in_paise = int(first_inst.amount * 100)
                        inst_desc = f"Payment for Installment #{first_inst.installment_number} of Lead #{lead.id}"
                        if len(inst_desc) > 200:
                            inst_desc = inst_desc[:197] + "..."
                            
                        inst_link_data = {
                            "amount": inst_amount_in_paise,
                            "currency": "INR",
                            "accept_partial": False,
                            "description": inst_desc,
                            "customer": {
                                "name": lead.name,
                                "email": lead.email,
                            },
                            "notify": {
                                "sms": False,
                                "email": False
                            },
                            "notes": {
                                "lead_id": str(lead.id),
                                "installment_id": str(first_inst.id)
                            }
                        }
                        if phone:
                            inst_link_data["customer"]["contact"] = phone
                            
                        try:
                            inst_payment_link = client.payment_link.create(inst_link_data)
                            payment_link_url = inst_payment_link.get('short_url')
                        except Exception as rzp_err:
                            print(f"Error creating installment payment link: {rzp_err}")
                            payment_link_url = ""
                            
                        rich_installments = [{
                            'installment_number': first_inst.installment_number,
                            'amount': first_inst.amount,
                            'status': first_inst.status,
                            'payment_link': payment_link_url,
                            'razorpay_payment_id': ''
                        }]
                        
                        for inst in lead.installments.exclude(id=first_inst.id).order_by('installment_number'):
                            rich_installments.append({
                                'installment_number': inst.installment_number,
                                'amount': inst.amount,
                                'status': inst.status,
                                'payment_link': '',
                                'razorpay_payment_id': ''
                            })
                            
                        rich_items = []
                        for item in lead.items.all():
                            req_item = lead.requirement.items.filter(subcategory=item.subcategory).first()
                            image_url = None
                            if req_item and req_item.image:
                                image_url = request.build_absolute_uri(req_item.image.url)
                            
                            rich_items.append({
                                'name': item.subcategory.name,
                                'count': item.count,
                                'price': float(req_item.total_amount) if req_item else 0.00,
                                'total_mrp': float(req_item.total_mrp) if req_item else 0.00,
                                'description': req_item.description if req_item else "",
                                'image_url': image_url
                            })
                            
                        from django.core.mail import EmailMultiAlternatives
                        from django.template.loader import render_to_string
                        
                        subject = f"Product Requirements & Payment Details - Lead #{lead.id}"
                        from_email = settings.DEFAULT_FROM_EMAIL
                        to_email = [lead.email]
                        
                        html_content = render_to_string('cyborgapp/emails/lead_payment_share.html', {
                            'lead': lead,
                            'payment_link': payment_link_url,
                            'rich_items': rich_items,
                            'rich_installments': rich_installments
                        })
                        
                        msg = EmailMultiAlternatives(subject, "", from_email, to_email)
                        msg.attach_alternative(html_content, "text/html")
                        msg.send(fail_silently=False)
                        
                        LeadUpdate.objects.create(
                            lead=lead,
                            update_text=f"SYSTEM: Lead confirmed by {request.user.get_usertype_display().upper()}. First installment details and payment link shared with customer."
                        )
                        
                        return JsonResponse({'status': 'success', 'message': 'Installment details and payment link shared with customer successfully.'})

                    if not razorpay_payment_id:
                        # Step 1: Initialize installments and first payment
                        lead.payment_mode = 'part'
                        lead.save()
                        # Delete existing ones just in case we are retrying / re-splitting
                        lead.installments.all().delete()
                        for idx, amt in enumerate(parsed_installments, start=1):
                            LeadInstallment.objects.create(
                                lead=lead,
                                installment_number=idx,
                                amount=amt,
                                status='pending',
                                due_date=parsed_dates[idx-1]
                            )

                        first_inst = lead.installments.first()
                        amount_in_paise = int(first_inst.amount * 100)
                        if amount_in_paise > 0:
                            order_data = {
                                'amount': amount_in_paise,
                                'currency': 'INR',
                                'payment_capture': '1',
                                'notes': {
                                    'lead_id': str(lead.id),
                                    'installment_id': str(first_inst.id),
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
                                'lead_phone': lead.phone,
                                'payment_mode': 'part'
                            })
                        else:
                            # If first installment is somehow 0, proceed directly
                            pass
                    else:
                        # Step 2: Verify first installment payment
                        try:
                            client.utility.verify_payment_signature({
                                'razorpay_order_id': razorpay_order_id,
                                'razorpay_payment_id': razorpay_payment_id,
                                'razorpay_signature': razorpay_signature
                            })
                        except razorpay.errors.SignatureVerificationError:
                            return JsonResponse({'status': 'error', 'message': 'Payment verification failed.'}, status=400)

                        first_inst = lead.installments.first()
                        if first_inst:
                            first_inst.status = 'paid'
                            first_inst.razorpay_payment_id = razorpay_payment_id
                            first_inst.save()

                        LeadUpdate.objects.create(
                            lead=lead, 
                            update_text=f"First installment of ₹{first_inst.amount} paid successfully. Payment ID: {razorpay_payment_id}"
                        )
                        lead.razorpay_payment_id = razorpay_payment_id
                else:
                    # Single payment
                    if not razorpay_payment_id:
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
                                'lead_phone': lead.phone,
                                'payment_mode': 'single'
                            })
                        else:
                            pass
                    else:
                        try:
                            client.utility.verify_payment_signature({
                                'razorpay_order_id': razorpay_order_id,
                                'razorpay_payment_id': razorpay_payment_id,
                                'razorpay_signature': razorpay_signature
                            })
                        except razorpay.errors.SignatureVerificationError:
                            return JsonResponse({'status': 'error', 'message': 'Payment verification failed.'}, status=400)
                        
                        LeadUpdate.objects.create(
                            lead=lead, 
                            update_text=f"Payment verified successfully. Payment ID: {razorpay_payment_id}"
                        )
                        lead.razorpay_payment_id = razorpay_payment_id

                # Save static total amount and distribute commission (only once upon confirmation)
                lead.payment_mode = payment_mode
                lead.total_amount = lead.get_total_amount
                lead.confirmed_by = request.user
                from .utils import distribute_product_sale_commission
                if lead.payment_mode == 'part':
                    first_inst = lead.installments.order_by('installment_number').first()
                    distribute_product_sale_commission(lead, installment=first_inst)
                else:
                    distribute_product_sale_commission(lead)
            
            lead.status = new_status
            lead.save()
            
        if pass_lead and lead.status == 'pending':
            # Block passing if requirement is not approved
            if lead.requirement.status != 'approved':
                return JsonResponse({'status': 'error', 'message': 'Requirement for this lead is not in approved state. Currently cannot update, contact admin.'}, status=403)
            
            levels = ['marketing', 'mandalam', 'district', 'superadmin']
            LEVEL_DISPLAY_MAP = {
                'marketing': 'DIGITAL FRANCHISE',
                'mandalam': 'FECILITATION CENTER',
                'district': 'DISTRICT FRANCHISE',
                'superadmin': 'SUPERADMIN'
            }
            try:
                current_idx = levels.index(lead.current_level)
                if current_idx < len(levels) - 1:
                    old_level = lead.current_level
                    lead.current_level = levels[current_idx + 1]
                    old_display = LEVEL_DISPLAY_MAP.get(old_level, old_level.upper())
                    new_display = LEVEL_DISPLAY_MAP.get(lead.current_level, lead.current_level.upper())
                    LeadUpdate.objects.create(lead=lead, update_text=f"SYSTEM: Lead passed from {old_display} to {new_display}")
            except ValueError:
                pass
                
        # Trigger Notification
        notification_actions = []
        if update_text:
            notification_actions.append("added an update")
        if new_status == 'confirmed':
            notification_actions.append("confirmed the lead")
        elif pass_lead:
            notification_actions.append("escalated the lead")
            
        if notification_actions:
            action_desc = " & ".join(notification_actions)
            create_lead_notification(request.user, lead, f"{action_desc} for '{lead.name}'")

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
        
    installments_data = []
    for inst in lead.installments.all().order_by('installment_number'):
        installments_data.append({
            'id': inst.id,
            'installment_number': inst.installment_number,
            'amount': float(inst.amount),
            'status': inst.status,
            'razorpay_payment_id': inst.razorpay_payment_id or '',
            'due_date': inst.due_date.strftime('%Y-%m-%d') if inst.due_date else ''
        })
    
    return JsonResponse({
        'status': 'success',
        'updates': updates,
        'lead_status': lead.status,
        'current_level': lead.current_level,
        'can_update': can_update,
        'requirement_approved': requirement_approved,
        'total_amount': float(lead.get_total_amount),
        'payment_mode': lead.payment_mode,
        'installments': installments_data,
        'confirmed_by_id': lead.confirmed_by_id
    })

@login_required(login_url='login')
def lead_get_associate_updates(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    from .models import LeadAssociateUpdate
    updates = []
    for u in lead.associate_updates.select_related('user').all().order_by('-created_at'):
        updates.append({
            'update_text': u.update_text,
            'username': u.user.username,
            'user_name': u.user.name or u.user.username,
            'created_at': u.created_at.strftime('%b %d, %Y %H:%M')
        })
        
    can_add = (request.user.usertype == 'customer' and lead.requirement.customer == request.user)
    
    return JsonResponse({
        'status': 'success',
        'updates': updates,
        'can_add': can_add
    })

@login_required(login_url='login')
def lead_add_associate_update(request, lead_id):
    lead = get_object_or_404(Lead, id=lead_id)
    
    # Permission check: must be a customer (Associate Company) and the owner of the lead's requirement
    if request.user.usertype != 'customer' or lead.requirement.customer != request.user:
        return JsonResponse({'status': 'error', 'message': 'Permission denied.'}, status=403)
        
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        update_text = data.get('update_text')
        
        if not update_text:
            return JsonResponse({'status': 'error', 'message': 'Update text is required.'}, status=400)
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

        from .models import LeadAssociateUpdate
        if created_at:
            LeadAssociateUpdate.objects.create(
                lead=lead,
                user=request.user,
                update_text=update_text,
                created_at=created_at
            )
        else:
            LeadAssociateUpdate.objects.create(
                lead=lead,
                user=request.user,
                update_text=update_text
            )
        # Trigger Notification
        create_lead_notification(request.user, lead, f"added an associate update for '{lead.name}'")

        return JsonResponse({'status': 'success', 'message': 'Associate update added successfully.'})
        
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=400)

@login_required(login_url='login')
def pay_installment(request, installment_id):
    from .models import LeadInstallment
    installment = get_object_or_404(LeadInstallment, id=installment_id)

    # Allow any logged-in user to pay installment payments
    if not installment.lead.confirmed_by_id:
        installment.lead.confirmed_by = request.user
        installment.lead.save()
    
    # Check that previous installments are paid!
    previous_pending = LeadInstallment.objects.filter(
        lead=installment.lead,
        installment_number__lt=installment.installment_number,
        status='pending'
    ).exists()
    if previous_pending:
        return JsonResponse({'status': 'error', 'message': 'Please pay the previous installments first.'}, status=400)

    if installment.status == 'paid':
        return JsonResponse({'status': 'error', 'message': 'Installment is already paid.'}, status=400)
    
    import razorpay
    from django.conf import settings
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    
    amount_in_paise = int(installment.amount * 100)
    order_data = {
        'amount': amount_in_paise,
        'currency': 'INR',
        'payment_capture': '1',
        'notes': {
            'lead_id': str(installment.lead.id),
            'installment_id': str(installment.id),
            'project': 'onmarketing'
        }
    }
    razorpay_order = client.order.create(data=order_data)
    
    return JsonResponse({
        'status': 'payment_required',
        'order_id': razorpay_order['id'],
        'amount': amount_in_paise,
        'key': settings.RAZORPAY_KEY_ID,
        'lead_name': installment.lead.name,
        'lead_email': installment.lead.email,
        'lead_phone': installment.lead.phone
    })

@login_required(login_url='login')
def verify_installment_payment(request, installment_id):
    from .models import LeadInstallment
    installment = get_object_or_404(LeadInstallment, id=installment_id)
    if installment.status == 'paid':
        return JsonResponse({'status': 'success', 'message': 'Installment already marked as paid.'})
        
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_signature = data.get('razorpay_signature')
        
        import razorpay
        from django.conf import settings
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        
        try:
            client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            })
        except razorpay.errors.SignatureVerificationError:
            return JsonResponse({'status': 'error', 'message': 'Payment verification failed.'}, status=400)
            
        installment.status = 'paid'
        installment.razorpay_payment_id = razorpay_payment_id
        installment.save()
        
        from .utils import distribute_product_sale_commission
        distribute_product_sale_commission(installment.lead, installment=installment)
        
        # If this is the first installment and lead is still pending, confirm the lead
        lead = installment.lead
        if installment.installment_number == 1 and lead.status == 'pending':
            lead.total_amount = lead.get_total_amount
            lead.razorpay_payment_id = razorpay_payment_id
            lead.payment_mode = 'part'
            lead.status = 'confirmed'
            lead.save()

        LeadUpdate.objects.create(
            lead=lead,
            update_text=f"Installment {installment.installment_number} of ₹{installment.amount} paid successfully. Payment ID: {razorpay_payment_id}"
        )
        
        return JsonResponse({'status': 'success', 'message': 'Installment paid successfully.'})
    return JsonResponse({'status': 'error'}, status=400)

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


@login_required(login_url='login')
def get_mandalams_by_district(request):
    """AJAX endpoint: returns mandalam users under a given district for staff FC assignment.
    Marks FCs already assigned to another staff so the UI can disable them.
    Accepts optional exclude_staff_id so the being-edited staff's own FCs remain selectable.
    """
    if request.user.usertype != 'superadmin':
        return JsonResponse({'status': 'error', 'message': 'Permission denied.'}, status=403)
    district_id = request.GET.get('district_id')
    if not district_id:
        return JsonResponse({'status': 'error', 'message': 'district_id is required.'}, status=400)

    # Optional: when editing an existing staff user, exclude that user's own assignments
    exclude_staff_id = request.GET.get('exclude_staff_id', '')

    # Collect FC IDs already assigned to ANY staff (excluding the current one being edited)
    from django.db.models import Q
    taken_fc_ids = set(
        CustomUser.objects.filter(usertype='staff')
        .exclude(id=exclude_staff_id if exclude_staff_id else None)
        .values_list('assigned_facilitation_centers__id', flat=True)
    ) - {None}

    mandalams_qs = CustomUser.objects.filter(
        usertype='mandalam', assigned_district_id=district_id, is_active=True
    ).order_by('name')

    mandalams = []
    for m in mandalams_qs:
        mandalams.append({
            'id': m.id,
            'name': m.name,
            'email': m.email,
            'already_assigned': m.id in taken_fc_ids,
        })
    return JsonResponse({'status': 'success', 'mandalams': mandalams})


@login_required(login_url='login')
def superadmin_gst(request):
    if request.user.usertype != 'superadmin':
        messages.error(request, 'Permission denied.')
        return redirect('wallet_dashboard')
        
    from .models import Lead, WithdrawalRequest
    
    # Calculate total earned gst
    confirmed_leads = Lead.objects.filter(status='confirmed').order_by('-created_at')
    total_earned_gst = sum(lead.get_gst_amount for lead in confirmed_leads)
    
    # Calculate withdrawn gst
    withdrawn_gst = sum(r.amount for r in WithdrawalRequest.objects.filter(request_type='gst', status='approved'))
    
    # Balance
    balance_gst = total_earned_gst - withdrawn_gst
    
    # Withdrawal requests for GST
    withdrawal_requests = WithdrawalRequest.objects.filter(request_type='gst').order_by('-created_at')
    
    # Prepare lead list with their specific GST amount
    lead_items = []
    for lead in confirmed_leads:
        gst_amt = lead.get_gst_amount
        if gst_amt > 0:
            lead_items.append({
                'lead': lead,
                'amount': gst_amt,
            })
            
    context = {
        'balance': balance_gst,
        'total_earned': total_earned_gst,
        'withdrawn_amount': withdrawn_gst,
        'withdrawal_requests': withdrawal_requests,
        'lead_items': lead_items,
        'page_type': 'GST',
    }
    return render(request, 'cyborgapp/superadmin/gst_dashboard.html', context)


@login_required(login_url='login')
def superadmin_expenses(request):
    if request.user.usertype != 'superadmin':
        messages.error(request, 'Permission denied.')
        return redirect('wallet_dashboard')
        
    from .models import Lead, WithdrawalRequest
    
    # Calculate total earned expenses
    confirmed_leads = Lead.objects.filter(status='confirmed').order_by('-created_at')
    total_earned_expense = sum(lead.get_expense_amount for lead in confirmed_leads)
    
    # Calculate withdrawn expenses
    withdrawn_expense = sum(r.amount for r in WithdrawalRequest.objects.filter(request_type='expense', status='approved'))
    
    # Balance
    balance_expense = total_earned_expense - withdrawn_expense
    
    # Withdrawal requests for Expenses
    withdrawal_requests = WithdrawalRequest.objects.filter(request_type='expense').order_by('-created_at')
    
    # Prepare lead list with their specific Expense amount
    lead_items = []
    for lead in confirmed_leads:
        expense_amt = lead.get_expense_amount
        if expense_amt > 0:
            lead_items.append({
                'lead': lead,
                'amount': expense_amt,
            })
            
    context = {
        'balance': balance_expense,
        'total_earned': total_earned_expense,
        'withdrawn_amount': withdrawn_expense,
        'withdrawal_requests': withdrawal_requests,
        'lead_items': lead_items,
        'page_type': 'Expenses',
    }
    return render(request, 'cyborgapp/superadmin/expenses_dashboard.html', context)


@login_required(login_url='login')
def request_gst_withdrawal(request):
    if request.user.usertype != 'superadmin':
        messages.error(request, 'Permission denied.')
        return redirect('superadmin_gst')
        
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
            
        from .models import Lead, WithdrawalRequest
        total_earned_gst = sum(lead.get_gst_amount for lead in Lead.objects.filter(status='confirmed'))
        withdrawn_gst = sum(r.amount for r in WithdrawalRequest.objects.filter(request_type='gst', status='approved'))
        balance_gst = total_earned_gst - withdrawn_gst
        
        if amount > balance_gst:
            messages.error(request, 'Insufficient balance.')
        elif amount <= 0:
            messages.error(request, 'Invalid amount.')
        else:
            WithdrawalRequest.objects.create(
                user=request.user,
                amount=amount,
                request_type='gst',
                account_number=acc_num,
                ifsc_code=ifsc,
                account_holder=holder,
                phone_linked=phone
            )
            request.user.bank_account_number = acc_num
            request.user.bank_ifsc = ifsc
            request.user.bank_account_holder = holder
            request.user.bank_phone = phone
            request.user.save()
            messages.success(request, 'GST withdrawal request submitted successfully.')
            
    return redirect('superadmin_gst')


@login_required(login_url='login')
def request_expense_withdrawal(request):
    if request.user.usertype != 'superadmin':
        messages.error(request, 'Permission denied.')
        return redirect('superadmin_expenses')
        
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
            
        from .models import Lead, WithdrawalRequest
        total_earned_expense = sum(lead.get_expense_amount for lead in Lead.objects.filter(status='confirmed'))
        withdrawn_expense = sum(r.amount for r in WithdrawalRequest.objects.filter(request_type='expense', status='approved'))
        balance_expense = total_earned_expense - withdrawn_expense
        
        if amount > balance_expense:
            messages.error(request, 'Insufficient balance.')
        elif amount <= 0:
            messages.error(request, 'Invalid amount.')
        else:
            WithdrawalRequest.objects.create(
                user=request.user,
                amount=amount,
                request_type='expense',
                account_number=acc_num,
                ifsc_code=ifsc,
                account_holder=holder,
                phone_linked=phone
            )
            request.user.bank_account_number = acc_num
            request.user.bank_ifsc = ifsc
            request.user.bank_account_holder = holder
            request.user.bank_phone = phone
            request.user.save()
            messages.success(request, 'Expense withdrawal request submitted successfully.')
            
    return redirect('superadmin_expenses')


@login_required(login_url='login')
def superadmin_leaderboard(request):
    if request.user.usertype != 'superadmin':
        messages.error(request, 'Permission denied.')
        return redirect('login')
        
    from datetime import datetime
    from django.db.models import Sum, Count, Q
    from .models import Lead, LeadInstallment, CustomUser
    
    # Get current date info
    now = datetime.now()
    current_year = now.year
    current_month = now.month
    
    # Get filters
    year_param = request.GET.get('year', str(current_year))
    month_param = request.GET.get('month', str(current_month))
    day_param = request.GET.get('day', 'all')
    metric = request.GET.get('metric', 'amount') # 'amount' or 'count'
    
    # Convert year
    try:
        year = int(year_param)
    except ValueError:
        year = current_year
        
    # Convert month
    if month_param == 'all':
        month = 'all'
    else:
        try:
            month = int(month_param)
        except ValueError:
            month = current_month
            
    # Convert day
    if day_param == 'all':
        day = 'all'
    else:
        try:
            day = int(day_param)
        except ValueError:
            day = 'all'
            
    # Generate list of years
    years = list(range(2024, current_year + 2))
    
    # Generate months list
    months = [
        (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
        (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
        (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December')
    ]
    
    # Generate days list
    days = list(range(1, 32))
    
    # Fetch qualified leads
    leads_qs = Lead.objects.filter(status='confirmed').filter(
        Q(payment_mode='single') |
        Q(payment_mode='part', installments__status='paid')
    ).distinct()
    
    # Apply date filters
    if year:
        leads_qs = leads_qs.filter(created_at__year=year)
    if month != 'all':
        leads_qs = leads_qs.filter(created_at__month=month)
    if day != 'all':
        leads_qs = leads_qs.filter(created_at__day=day)
        
    # Fetch all relevant users
    users = CustomUser.objects.filter(usertype__in=['district', 'mandalam', 'marketing'])
    
    # Pre-fetch stats
    marketing_stats = leads_qs.values('marketing_user_id').annotate(
        lead_count=Count('id'),
        sales_sum=Sum('total_amount')
    )
    m_stats_map = {item['marketing_user_id']: item for item in marketing_stats if item['marketing_user_id']}
    
    mandalam_stats = leads_qs.values('marketing_user__assigned_mandalam_id').annotate(
        lead_count=Count('id'),
        sales_sum=Sum('total_amount')
    )
    man_stats_map = {item['marketing_user__assigned_mandalam_id']: item for item in mandalam_stats if item['marketing_user__assigned_mandalam_id']}
    
    district_stats = leads_qs.values('marketing_user__assigned_district_id').annotate(
        lead_count=Count('id'),
        sales_sum=Sum('total_amount')
    )
    d_stats_map = {item['marketing_user__assigned_district_id']: item for item in district_stats if item['marketing_user__assigned_district_id']}
    
    # Build list of users with stats
    user_data_list = []
    for u in users:
        lead_count = 0
        sales_sum = 0
        if u.usertype == 'marketing':
            stats = m_stats_map.get(u.id)
            if stats:
                lead_count = stats['lead_count']
                sales_sum = stats['sales_sum']
        elif u.usertype == 'mandalam':
            stats = man_stats_map.get(u.id)
            if stats:
                lead_count = stats['lead_count']
                sales_sum = stats['sales_sum']
        elif u.usertype == 'district':
            stats = d_stats_map.get(u.id)
            if stats:
                lead_count = stats['lead_count']
                sales_sum = stats['sales_sum']
                
        user_data_list.append({
            'id': u.id,
            'name': u.name or u.username,
            'usertype': u.usertype,
            'usertype_display': u.get_usertype_display(),
            'lead_count': lead_count,
            'total_sales': float(sales_sum or 0),
        })
        
    # Get top performers per category
    if metric == 'count':
        sort_key = lambda x: (x['lead_count'], x['total_sales'])
    else:
        sort_key = lambda x: (x['total_sales'], x['lead_count'])
        
    top_district = None
    top_fc = None
    top_df = None
    
    districts_list = [u for u in user_data_list if u['usertype'] == 'district']
    fc_list = [u for u in user_data_list if u['usertype'] == 'mandalam']
    df_list = [u for u in user_data_list if u['usertype'] == 'marketing']
    
    if districts_list:
        best_d = max(districts_list, key=sort_key)
        if metric == 'count' and best_d['lead_count'] > 0:
            top_district = best_d
        elif metric == 'amount' and best_d['total_sales'] > 0:
            top_district = best_d
            
    if fc_list:
        best_fc = max(fc_list, key=sort_key)
        if metric == 'count' and best_fc['lead_count'] > 0:
            top_fc = best_fc
        elif metric == 'amount' and best_fc['total_sales'] > 0:
            top_fc = best_fc
            
    if df_list:
        best_df = max(df_list, key=sort_key)
        if metric == 'count' and best_df['lead_count'] > 0:
            top_df = best_df
        elif metric == 'amount' and best_df['total_sales'] > 0:
            top_df = best_df
            
    context = {
        'user_data_list': user_data_list,
        'top_district': top_district,
        'top_fc': top_fc,
        'top_df': top_df,
        'years': years,
        'months': months,
        'days': days,
        'selected_year': str(year),
        'selected_month': str(month_param),
        'selected_day': str(day_param),
        'selected_metric': metric,
    }
    
    return render(request, 'cyborgapp/superadmin/leaderboards.html', context)


@login_required(login_url='login')
def superadmin_export_leads(request):
    if request.user.usertype == 'staff':
        target_user = get_target_user(request)
        user = target_user
    else:
        user = request.user
        
    from django.db.models import Exists, OuterRef, Case, When, Value, IntegerField, Q
    from .models import Lead, LeadInstallment
    import csv
    
    pending_inst_sq = LeadInstallment.objects.filter(lead=OuterRef('pk'), status='pending')
    
    base_qs = Lead.objects.all().annotate(
        installment_pending=Exists(pending_inst_sq)
    ).exclude(
        status='confirmed',
        installment_pending=False
    )
    
    # Filter by user role (scoping leads to what the logged-in user is allowed to see)
    if user.usertype == 'superadmin':
        leads = base_qs
    elif user.usertype == 'marketing':
        leads = base_qs.filter(marketing_user=user)
    elif user.usertype == 'mandalam':
        leads = base_qs.filter(marketing_user__assigned_mandalam=user)
    elif user.usertype == 'district':
        leads = base_qs.filter(marketing_user__assigned_district=user)
    elif user.usertype == 'manager':
        if user.assigned_district:
            leads = base_qs.filter(marketing_user__assigned_district=user.assigned_district)
        else:
            leads = base_qs.filter(marketing_user__created_by=user)
    elif user.usertype == 'customer':
        leads = base_qs.filter(requirement__customer=user)
    else:
        leads = Lead.objects.none()

    # Apply date filtration:
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    if from_date:
        try:
            leads = leads.filter(created_at__date__gte=from_date)
        except Exception:
            pass
    if to_date:
        try:
            leads = leads.filter(created_at__date__lte=to_date)
        except Exception:
            pass

    leads = leads.order_by('-created_at')
    
    if request.GET.get('check_empty') == 'true':
        return JsonResponse({'empty': not leads.exists()})
        
    queryset = leads.iterator(chunk_size=2000)
    
    def csv_generator():
        echo_buffer = Echo()
        writer = csv.writer(echo_buffer)
        
        yield writer.writerow([
            'Lead ID', 'Lead Name', 'Phone', 'Email', 'Address', 'Remarks',
            'Requirement', 'Category', 'Digital Franchise', 'Facilitation Center',
            'District Franchise', 'Status', 'Current Level', 'Total Amount (Rs)', 'Payment Mode', 'Created At'
        ])
        
        for lead in queryset:
            fc = lead.marketing_user.assigned_mandalam.name if (lead.marketing_user and lead.marketing_user.assigned_mandalam) else 'N/A'
            df = lead.marketing_user.assigned_district.name if (lead.marketing_user and lead.marketing_user.assigned_district) else 'N/A'
            
            yield writer.writerow([
                lead.id,
                lead.name,
                lead.phone,
                lead.email or '',
                lead.address or '',
                lead.remarks or '',
                f"{lead.requirement.title} (By {lead.requirement.customer.name})" if (lead.requirement and lead.requirement.customer) else (lead.requirement.title if lead.requirement else 'N/A'),
                lead.requirement.category.name if (lead.requirement and lead.requirement.category) else 'N/A',
                lead.marketing_user.name or lead.marketing_user.username if lead.marketing_user else 'N/A',
                fc,
                df,
                'Payment Pending' if (lead.status == 'confirmed' and lead.payment_mode == 'part' and lead.installment_pending) else lead.get_status_display(),
                lead.get_current_level_display(),
                float(lead.get_total_amount),
                lead.get_payment_mode_display(),
                lead.created_at.strftime('%Y-%m-%d %H:%M:%S')
            ])
            
    response = StreamingHttpResponse(csv_generator(), content_type="text/csv")
    response['Content-Disposition'] = 'attachment; filename="leads_export.csv"'
    return response


@login_required(login_url='login')
def superadmin_export_confirmed_leads(request):
    if request.user.usertype == 'staff':
        target_user = get_target_user(request)
        user = target_user
    else:
        user = request.user
        
    from django.db.models import Exists, OuterRef, Case, When, Value, IntegerField, Q
    from .models import Lead, LeadInstallment
    import csv
    
    pending_inst_sq = LeadInstallment.objects.filter(lead=OuterRef('pk'), status='pending')
    
    base_qs = Lead.objects.filter(status='confirmed').filter(
        Q(payment_mode='single') | 
        Q(payment_mode='part', installments__installment_number=1, installments__status='paid')
    ).distinct().annotate(
        installment_pending=Exists(pending_inst_sq)
    )
    
    # Filter by user role (scoping leads to what the logged-in user is allowed to see)
    if user.usertype == 'superadmin':
        leads = base_qs
    elif user.usertype == 'marketing':
        leads = base_qs.filter(marketing_user=user)
    elif user.usertype == 'mandalam':
        leads = base_qs.filter(marketing_user__assigned_mandalam=user)
    elif user.usertype == 'district':
        leads = base_qs.filter(marketing_user__assigned_district=user)
    elif user.usertype == 'manager':
        if user.assigned_district:
            leads = base_qs.filter(marketing_user__assigned_district=user.assigned_district)
        else:
            leads = base_qs.filter(marketing_user__created_by=user)
    elif user.usertype == 'customer':
        leads = base_qs.filter(requirement__customer=user)
    else:
        leads = Lead.objects.none()

    # Apply date filtration:
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    if from_date:
        try:
            leads = leads.filter(created_at__date__gte=from_date)
        except Exception:
            pass
    if to_date:
        try:
            leads = leads.filter(created_at__date__lte=to_date)
        except Exception:
            pass

    leads = leads.order_by('-created_at')
    
    if request.GET.get('check_empty') == 'true':
        return JsonResponse({'empty': not leads.exists()})
        
    queryset = leads.iterator(chunk_size=2000)
    
    def csv_generator():
        echo_buffer = Echo()
        writer = csv.writer(echo_buffer)
        
        yield writer.writerow([
            'Lead ID', 'Lead Name', 'Phone', 'Email', 'Address', 'Remarks',
            'Requirement', 'Category', 'Digital Franchise', 'Facilitation Center',
            'District Franchise', 'Status', 'Payment Mode', 'Pending Installments', 'Total Amount (Rs)', 'Created At'
        ])
        
        for lead in queryset:
            fc = lead.marketing_user.assigned_mandalam.name if (lead.marketing_user and lead.marketing_user.assigned_mandalam) else 'N/A'
            df = lead.marketing_user.assigned_district.name if (lead.marketing_user and lead.marketing_user.assigned_district) else 'N/A'
            
            yield writer.writerow([
                lead.id,
                lead.name,
                lead.phone,
                lead.email or '',
                lead.address or '',
                lead.remarks or '',
                f"{lead.requirement.title} (By {lead.requirement.customer.name})" if (lead.requirement and lead.requirement.customer) else (lead.requirement.title if lead.requirement else 'N/A'),
                lead.requirement.category.name if (lead.requirement and lead.requirement.category) else 'N/A',
                lead.marketing_user.name or lead.marketing_user.username if lead.marketing_user else 'N/A',
                fc,
                df,
                'Payment Pending' if (lead.payment_mode == 'part' and lead.installment_pending) else 'Confirmed',
                lead.get_payment_mode_display(),
                'Yes' if lead.installment_pending else 'No',
                float(lead.get_total_amount),
                lead.created_at.strftime('%Y-%m-%d %H:%M:%S')
            ])
            
    response = StreamingHttpResponse(csv_generator(), content_type="text/csv")
    response['Content-Disposition'] = 'attachment; filename="confirmed_leads_export.csv"'
    return response


@login_required(login_url='login')
def export_withdrawal_requests_csv(request):
    if request.user.usertype != 'superadmin':
        return HttpResponse("Forbidden", status=403)
        
    from .models import WithdrawalRequest
    import csv
    
    reqs = WithdrawalRequest.objects.all()
    
    # Apply date filtration
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    if from_date:
        try:
            reqs = reqs.filter(created_at__date__gte=from_date)
        except Exception:
            pass
    if to_date:
        try:
            reqs = reqs.filter(created_at__date__lte=to_date)
        except Exception:
            pass
            
    reqs = reqs.order_by('-created_at')
    
    if request.GET.get('check_empty') == 'true':
        return JsonResponse({'empty': not reqs.exists()})
    
    # keeping memory usage low for lakhs of requests by streaming and chunking database calls
    queryset = reqs.iterator(chunk_size=2000)
    
    def csv_generator():
        echo_buffer = Echo()
        writer = csv.writer(echo_buffer)
        
        # Write CSV Header
        yield writer.writerow([
            'Request ID', 'User Name', 'User Role', 'Amount (Rs)', 'Status', 'Request Type',
            'Date Created', 'Account Number', 'IFSC Code', 'Account Holder', 'Phone Linked', 'Remarks'
        ])
        
        for r in queryset:
            yield writer.writerow([
                r.id,
                r.user.name if r.user else 'N/A',
                r.user.get_usertype_display() if r.user else 'N/A',
                float(r.amount),
                r.status,
                r.get_request_type_display() if hasattr(r, 'get_request_type_display') else r.request_type,
                r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                r.account_number or 'N/A',
                r.ifsc_code or 'N/A',
                r.account_holder or 'N/A',
                r.phone_linked or 'N/A',
                r.remarks or ''
            ])
            
    response = StreamingHttpResponse(csv_generator(), content_type="text/csv")
    response['Content-Disposition'] = 'attachment; filename="withdrawal_requests.csv"'
    return response


@login_required(login_url='login')
def get_notifications(request):
    from .models import Notification
    notes = Notification.objects.filter(recipient=request.user)[:15]
    unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    
    data = []
    for note in notes:
        actor_name = note.actor.name or note.actor.username if note.actor else "System"
        data.append({
            'id': note.id,
            'actor': actor_name,
            'verb': note.verb,
            'is_read': note.is_read,
            'created_at': note.created_at.strftime('%b %d, %Y %I:%M %p'),
            'lead_id': note.lead.id if note.lead else None
        })
        
    return JsonResponse({
        'status': 'success',
        'notifications': data,
        'unread_count': unread_count
    })


@login_required(login_url='login')
def mark_notifications_read(request):
    from .models import Notification
    if request.method == 'POST':
        import json
        try:
            body = json.loads(request.body)
            note_id = body.get('notification_id')
        except Exception:
            note_id = None
            
        if note_id:
            Notification.objects.filter(recipient=request.user, id=note_id).update(is_read=True)
        else:
            Notification.objects.filter(recipient=request.user).update(is_read=True)
            
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=400)


