from .models import WithdrawalRequest

def wallet_context(request):
    context = {
        'global_pending_withdrawals_count': 0,
        'monitored_fc': None,
        'assigned_fcs': []
    }

    if request.user.is_authenticated:
        if request.user.usertype == 'superadmin':
            pending_count = WithdrawalRequest.objects.filter(status='pending').count()
            context['global_pending_withdrawals_count'] = pending_count

        elif request.user.usertype == 'staff':
            # Handle clear_fc flag
            if 'clear_fc' in request.GET:
                request.session.pop('staff_monitored_fc_id', None)

            # If explicitly passed via GET, update the session
            fc_id = request.GET.get('fc_id')
            if fc_id:
                if request.user.assigned_facilitation_centers.filter(id=fc_id).exists():
                    request.session['staff_monitored_fc_id'] = fc_id

            # Retrieve from session
            monitored_fc_id = request.session.get('staff_monitored_fc_id')
            if monitored_fc_id:
                try:
                    context['monitored_fc'] = request.user.assigned_facilitation_centers.get(id=monitored_fc_id)
                except Exception:
                    request.session.pop('staff_monitored_fc_id', None)

            # Expose all assigned FCs
            context['assigned_fcs'] = request.user.assigned_facilitation_centers.all().order_by('name')

    return context
