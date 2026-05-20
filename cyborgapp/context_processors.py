from .models import WithdrawalRequest

def wallet_context(request):
    if request.user.is_authenticated and request.user.usertype == 'superadmin':
        pending_count = WithdrawalRequest.objects.filter(status='pending').count()
        return {
            'global_pending_withdrawals_count': pending_count
        }
    return {
        'global_pending_withdrawals_count': 0
    }
