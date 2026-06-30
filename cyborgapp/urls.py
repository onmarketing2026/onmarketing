from . import views
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Password Reset URLs
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='cyborgapp/registration/password_reset_form.html',
        form_class=views.CustomPasswordResetForm
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='cyborgapp/registration/password_reset_done.html'), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', views.CustomPasswordResetConfirmView.as_view(template_name='cyborgapp/registration/password_reset_confirm.html'), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(template_name='cyborgapp/registration/password_reset_complete.html'), name='password_reset_complete'),
    
    path('superadmin/dashboard/', views.superadmin_dashboard, name='superadmin_dashboard'),
    path('profile/', views.profile_view, name='profile'),
    path('profile/bank-details/update/', views.update_bank_details, name='update_bank_details'),
    path('superadmin/users/', views.superadmin_users, name='superadmin_users'),
    path('superadmin/users/create/', views.superadmin_user_create, name='superadmin_user_create'),
    path('superadmin/users/<int:user_id>/edit/', views.superadmin_user_edit, name='superadmin_user_edit'),
    path('superadmin/users/<int:user_id>/delete/', views.superadmin_user_delete, name='superadmin_user_delete'),
    path('superadmin/users/<int:user_id>/toggle/', views.superadmin_user_toggle_status, name='superadmin_user_toggle_status'),
    path('superadmin/users/check_email/', views.check_email, name='check_email'),
    
    # Requirement Management
    path('requirements/', views.requirement_list, name='requirement_list'),
    path('requirements/create/', views.requirement_create, name='requirement_create'),
    path('requirements/<int:req_id>/edit/', views.requirement_edit, name='requirement_edit'),
    path('requirements/<int:req_id>/detail/', views.requirement_detail, name='requirement_detail'),
    path('requirements/<int:req_id>/delete/', views.requirement_delete, name='requirement_delete'),
    path('requirements/<int:req_id>/toggle/', views.requirement_toggle_status, name='requirement_toggle_status'),
    path('requirements/<int:req_id>/lead/create/', views.lead_create, name='lead_create'),
    path('requirements/item/<int:item_id>/assign-mandalams/', views.assign_mandalams, name='assign_mandalams'),
    
    path('wallet/', views.wallet_dashboard, name='wallet_dashboard'),
    path('superadmin/user-wallets/', views.superadmin_user_wallets, name='superadmin_user_wallets'),
    path('superadmin/user-wallets/<int:user_id>/', views.wallet_dashboard, name='superadmin_user_view_wallet'),
    path('superadmin/user-wallets/<int:user_id>/transactions/', views.get_user_transactions, name='get_user_transactions'),
    path('wallet/withdraw/', views.request_withdrawal, name='request_withdrawal'),
    path('wallet/requests/', views.withdrawal_requests_list, name='withdrawal_requests_list'),
    path('wallet/requests/<int:request_id>/update/', views.update_withdrawal_status, name='update_withdrawal_status'),
    path('wallet/requests/<int:request_id>/delete/', views.delete_withdrawal_request, name='delete_withdrawal_request'),
    path('wallet/export-commissions-csv/', views.export_commissions_csv, name='export_commissions_csv'),
    path('superadmin/user-wallets/export-csv/', views.export_wallets_csv, name='export_wallets_csv'),
    path('superadmin/gst/', views.superadmin_gst, name='superadmin_gst'),
    path('superadmin/expenses/', views.superadmin_expenses, name='superadmin_expenses'),
    path('superadmin/gst/withdraw/', views.request_gst_withdrawal, name='request_gst_withdrawal'),
    path('superadmin/expenses/withdraw/', views.request_expense_withdrawal, name='request_expense_withdrawal'),

    path('leads/', views.lead_list, name='lead_list'),
    path('leads/confirmed/', views.confirmed_lead_list, name='confirmed_lead_list'),
    path('leads/<int:lead_id>/edit/', views.lead_edit, name='lead_edit'),
    path('leads/<int:lead_id>/update/', views.lead_add_update, name='lead_add_update'),
    path('leads/<int:lead_id>/updates/get/', views.lead_get_updates, name='lead_get_updates'),
    path('leads/<int:lead_id>/associate-updates/get/', views.lead_get_associate_updates, name='lead_get_associate_updates'),
    path('leads/<int:lead_id>/associate-updates/add/', views.lead_add_associate_update, name='lead_add_associate_update'),
    path('leads/<int:lead_id>/share/', views.share_lead_payment, name='share_lead_payment'),
    path('installments/<int:installment_id>/pay/', views.pay_installment, name='pay_installment'),
    path('installments/<int:installment_id>/verify/', views.verify_installment_payment, name='verify_installment_payment'),
    path('razorpay-webhook/', views.razorpay_webhook, name='razorpay_webhook'),
    path('superadmin/commissions/', views.commission_settings, name='commission_settings'),
    
    # Category Management
    path('superadmin/categories/', views.category_list, name='category_list'),
    path('superadmin/categories/create/', views.category_create, name='category_create'),
    path('superadmin/categories/<int:cat_id>/edit/', views.category_edit, name='category_edit'),
    path('superadmin/categories/<int:cat_id>/delete/', views.category_delete, name='category_delete'),
    path('api/get_subcategories/<int:cat_id>/', views.get_subcategories, name='get_subcategories'),
    path('api/get_mandalams_by_district/', views.get_mandalams_by_district, name='get_mandalams_by_district'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)