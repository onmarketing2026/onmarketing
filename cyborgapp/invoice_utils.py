"""
invoice_utils.py
Generates professional PDF invoices for leads (single-payment and part-payment)
and emails them to the lead's registered email address.
"""

import io
from datetime import datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)


# ─────────────────────────── colour palette ───────────────────────────
BRAND_DARK  = colors.HexColor('#1a1a2e')
BRAND_GOLD  = colors.HexColor('#c9a84c')
BRAND_GREEN = colors.HexColor('#198754')
BRAND_MUTED = colors.HexColor('#6c757d')
BRAND_BG    = colors.HexColor('#f8f9fa')
WHITE       = colors.white


# ─────────────────────────── style helpers ───────────────────────────
def _styles():
    base = getSampleStyleSheet()
    return {
        'h1': ParagraphStyle('h1', fontName='Helvetica-Bold', fontSize=22,
                             textColor=BRAND_DARK, spaceAfter=2),
        'sub': ParagraphStyle('sub', fontName='Helvetica', fontSize=9,
                              textColor=BRAND_MUTED, spaceAfter=2),
        'label': ParagraphStyle('label', fontName='Helvetica-Bold', fontSize=8,
                                textColor=BRAND_MUTED, leading=12),
        'value': ParagraphStyle('value', fontName='Helvetica', fontSize=9,
                                textColor=BRAND_DARK, leading=13),
        'bold': ParagraphStyle('bold', fontName='Helvetica-Bold', fontSize=9,
                               textColor=BRAND_DARK),
        'section': ParagraphStyle('section', fontName='Helvetica-Bold', fontSize=10,
                                  textColor=BRAND_DARK, spaceBefore=10, spaceAfter=4),
        'small': ParagraphStyle('small', fontName='Helvetica', fontSize=8,
                                textColor=BRAND_MUTED),
        'total_label': ParagraphStyle('tl', fontName='Helvetica-Bold', fontSize=11,
                                      textColor=BRAND_DARK, alignment=TA_RIGHT),
        'total_value': ParagraphStyle('tv', fontName='Helvetica-Bold', fontSize=13,
                                      textColor=BRAND_GREEN, alignment=TA_RIGHT),
        'footer': ParagraphStyle('footer', fontName='Helvetica', fontSize=8,
                                 textColor=BRAND_MUTED, alignment=TA_CENTER),
    }


def _fmt(amount):
    """Format a number as Indian Rupee string."""
    try:
        return f'\u20b9{Decimal(str(amount)):,.2f}'
    except Exception:
        return f'\u20b9{amount}'


# ─────────────────────── main PDF builder ────────────────────────────
def build_invoice_pdf(lead, installment=None):
    """
    Generate an invoice PDF and return it as bytes.

    Parameters
    ----------
    lead        : Lead model instance
    installment : LeadInstallment instance (for per-installment email invoices).
                  If None, a consolidated invoice for the whole lead is generated.

    Returns
    -------
    bytes – the raw PDF content
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )
    W = A4[0] - 30 * mm          # usable width
    s = _styles()
    story = []

    # ── header row: company left, invoice label right ──
    invoice_no = (
        f'INV-{lead.id:05d}-P{installment.installment_number}'
        if installment else f'INV-{lead.id:05d}'
    )
    invoice_date = (
        installment.updated_at.strftime('%d %b %Y')
        if installment else datetime.now().strftime('%d %b %Y')
    )
    header_data = [[
        Paragraph('<b>OnCyborg Pvt Ltd</b><br/>'
                  '<font color="#6c757d" size="8">oncyborgpvtltd@gmail.com</font>', s['h1']),
        Paragraph(
            f'<font color="#c9a84c"><b>INVOICE</b></font><br/>'
            f'<font size="8" color="#6c757d">#{invoice_no}</font><br/>'
            f'<font size="8" color="#6c757d">Date: {invoice_date}</font>',
            ParagraphStyle('ir', fontName='Helvetica-Bold', fontSize=18,
                           textColor=BRAND_GOLD, alignment=TA_RIGHT)
        ),
    ]]
    header_tbl = Table(header_data, colWidths=[W * 0.6, W * 0.4])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(header_tbl)
    story.append(HRFlowable(width='100%', thickness=1.5, color=BRAND_GOLD, spaceAfter=10))

    # ── bill-to / lead info ──
    req = lead.requirement
    cat_name = req.category.name if req.category else '—'
    info_data = [[
        [
            Paragraph('BILLED TO', s['label']),
            Paragraph(f'<b>{lead.name}</b>', s['value']),
            Paragraph(lead.phone, s['value']),
            Paragraph(lead.email or '—', s['value']),
            Paragraph(lead.address or '', s['small']),
        ],
        [
            Paragraph('PROJECT / REQUIREMENT', s['label']),
            Paragraph(f'<b>{req.title}</b>', s['value']),
            Paragraph(f'Category: {cat_name}', s['small']),
            Spacer(1, 4),
            Paragraph('PAYMENT MODE', s['label']),
            Paragraph(
                'Part Payment' if lead.payment_mode == 'part' else 'Full Payment',
                s['value']
            ),
        ],
    ]]
    info_tbl = Table(info_data, colWidths=[W * 0.5, W * 0.5])
    info_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_BG),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 10))

    # ── items table ──
    story.append(Paragraph('Items', s['section']))

    items_header = ['#', 'Item / Subcategory', 'Qty', 'Unit Price (₹)', 'GST %', 'Amount (₹)']
    items_rows = [items_header]
    subtotal = Decimal('0.00')

    for idx, lead_item in enumerate(lead.items.select_related('subcategory').all(), 1):
        req_item = req.items.filter(subcategory=lead_item.subcategory).first()
        if not req_item:
            continue
        is_count = req.category and req.category.cat_type == 'count'
        qty = lead_item.count if is_count else 1
        unit_price = req_item.customer_amount + req_item.admin_markup + req_item.other_expenses
        gst_pct = req_item.gst
        base = unit_price * (qty or 1)
        gst_amt = base * (gst_pct / Decimal('100'))
        line_total = base + gst_amt
        subtotal += line_total
        items_rows.append([
            str(idx),
            lead_item.subcategory.name,
            str(qty) if is_count else '1',
            _fmt(unit_price),
            f'{gst_pct}%',
            _fmt(line_total),
        ])

    col_w = [8 * mm, W - 8*mm - 18*mm - 28*mm - 14*mm - 28*mm, 18*mm, 28*mm, 14*mm, 28*mm]
    items_tbl = Table(items_rows, colWidths=col_w, repeatRows=1)
    items_tbl.setStyle(TableStyle([
        # header row
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_DARK),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        # data rows
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (2, 1), (-1, -1), 'CENTER'),
        ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
        ('ALIGN', (5, 1), (5, -1), 'RIGHT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, BRAND_BG]),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#dee2e6')),
        ('TOPPADDING', (0, 1), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 10))

    # ── installments section (for part-payment) ──
    installments_qs = lead.installments.all().order_by('installment_number')
    if installments_qs.exists():
        story.append(Paragraph('Payment Installments', s['section']))
        inst_header = ['Installment', 'Amount (₹)', 'Due Date', 'Status', 'Payment ID']
        inst_rows = [inst_header]
        paid_total = Decimal('0.00')
        for inst in installments_qs:
            status_text = 'Paid' if inst.status == 'paid' else 'Pending'
            due = inst.due_date.strftime('%d %b %Y') if inst.due_date else '—'
            inst_rows.append([
                f'Installment {inst.installment_number}',
                _fmt(inst.amount),
                due,
                status_text,
                inst.razorpay_payment_id or '—',
            ])
            if inst.status == 'paid':
                paid_total += inst.amount

        inst_col_w = [35*mm, 28*mm, 28*mm, 22*mm, W - 35*mm - 28*mm - 28*mm - 22*mm]
        inst_tbl = Table(inst_rows, colWidths=inst_col_w, repeatRows=1)
        inst_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#343a40')),
            ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ALIGN', (1, 1), (2, -1), 'CENTER'),
            ('ALIGN', (3, 1), (3, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, BRAND_BG]),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#dee2e6')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(inst_tbl)
        story.append(Spacer(1, 6))

        # colour-code paid rows green
        for row_idx, inst in enumerate(installments_qs, 1):
            if inst.status == 'paid':
                inst_tbl.setStyle(TableStyle([
                    ('TEXTCOLOR', (3, row_idx), (3, row_idx), BRAND_GREEN),
                    ('FONTNAME', (3, row_idx), (3, row_idx), 'Helvetica-Bold'),
                ]))

        # if this PDF is for a specific installment highlight it
        if installment:
            for row_idx, inst in enumerate(installments_qs, 1):
                if inst.id == installment.id:
                    inst_tbl.setStyle(TableStyle([
                        ('BACKGROUND', (0, row_idx), (-1, row_idx),
                         colors.HexColor('#d1e7dd')),
                    ]))

        story.append(Spacer(1, 4))

    # ── totals block ──
    if installment:
        this_payment_label = f'This Payment (Installment {installment.installment_number})'
        this_payment_value = _fmt(installment.amount)
    else:
        this_payment_label = 'Total Amount Paid'
        this_payment_value = _fmt(lead.total_amount or subtotal)

    totals_data = [
        [Paragraph('Subtotal', s['total_label']),
         Paragraph(_fmt(subtotal), ParagraphStyle('tv2', fontName='Helvetica', fontSize=10, alignment=TA_RIGHT))],
        [Paragraph(this_payment_label, s['total_label']),
         Paragraph(this_payment_value, s['total_value'])],
    ]
    # Add balance due if part payment
    if lead.payment_mode == 'part' and not installment:
        paid_so_far = sum(
            i.amount for i in lead.installments.filter(status='paid')
        )
        balance = (lead.total_amount or subtotal) - paid_so_far
        if balance > 0:
            totals_data.append([
                Paragraph('Balance Due', ParagraphStyle('bl', fontName='Helvetica-Bold',
                                                        fontSize=11, textColor=colors.HexColor('#dc3545'),
                                                        alignment=TA_RIGHT)),
                Paragraph(_fmt(balance), ParagraphStyle('bv', fontName='Helvetica-Bold',
                                                        fontSize=13, textColor=colors.HexColor('#dc3545'),
                                                        alignment=TA_RIGHT)),
            ])

    totals_tbl = Table(totals_data, colWidths=[W * 0.75, W * 0.25])
    totals_tbl.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEABOVE', (0, 0), (-1, 0), 0.5, colors.HexColor('#dee2e6')),
        ('LINEABOVE', (0, 1), (-1, 1), 1.5, BRAND_GOLD),
    ]))
    story.append(totals_tbl)

    # ── razorpay payment id reference ──
    pay_id = installment.razorpay_payment_id if installment else lead.razorpay_payment_id
    if pay_id:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f'Payment Reference ID: <b>{pay_id}</b>', s['small']
        ))

    # ── footer ──
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width='100%', thickness=0.5, color=BRAND_GOLD))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        'Thank you for choosing OnCyborg Pvt Ltd. '
        'For queries contact: oncyborgpvtltd@gmail.com',
        s['footer']
    ))
    story.append(Paragraph(
        'This is a computer-generated invoice and does not require a signature.',
        s['footer']
    ))

    doc.build(story)
    return buf.getvalue()


# ─────────────────────── email helper ────────────────────────────────
def send_invoice_email(lead, installment=None):
    """
    Generate the invoice PDF and send it to the lead's email.

    Guards:
    - If lead.email is blank, silently skip.
    - If invoice_sent flag is already True on lead (full payment) or
      installment (part payment), silently skip — prevents duplicate
      emails when both the Razorpay webhook AND client-side verification
      fire for the same payment.

    Parameters
    ----------
    lead        : Lead instance
    installment : LeadInstallment instance (for per-installment emails)
    """
    if not lead.email:
        return

    # ── Duplicate-send guard ──────────────────────────────────────────
    if installment:
        # Re-fetch from DB to get the authoritative flag value
        from .models import LeadInstallment
        updated = LeadInstallment.objects.filter(
            id=installment.id, invoice_sent=False
        ).update(invoice_sent=True)
        if updated == 0:
            # Another path (webhook/client) already sent this invoice
            return
    else:
        from .models import Lead as LeadModel
        updated = LeadModel.objects.filter(
            id=lead.id, invoice_sent=False
        ).update(invoice_sent=True)
        if updated == 0:
            return
    # ─────────────────────────────────────────────────────────────────

    from django.core.mail import EmailMessage
    from django.conf import settings

    pdf_bytes = build_invoice_pdf(lead, installment=installment)

    if installment:
        subject = (
            f'Invoice – {lead.requirement.title} '
            f'(Installment {installment.installment_number}) | OnCyborg Pvt Ltd'
        )
        filename = f'Invoice_Lead{lead.id}_Installment{installment.installment_number}.pdf'
        body = (
            f'Dear {lead.name},\n\n'
            f'Please find attached your invoice for Installment '
            f'{installment.installment_number} of \u20b9{installment.amount} '
            f'against the project "{lead.requirement.title}".\n\n'
            f'Thank you for your payment.\n\n'
            f'Regards,\nOnCyborg Pvt Ltd'
        )
    else:
        subject = f'Invoice – {lead.requirement.title} | OnCyborg Pvt Ltd'
        filename = f'Invoice_Lead{lead.id}.pdf'
        body = (
            f'Dear {lead.name},\n\n'
            f'Please find attached your invoice for the project '
            f'"{lead.requirement.title}".\n\n'
            f'Thank you for your payment.\n\n'
            f'Regards,\nOnCyborg Pvt Ltd'
        )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[lead.email],
    )
    email.attach(filename, pdf_bytes, 'application/pdf')

    try:
        email.send(fail_silently=False)
    except Exception:
        # Roll back the flag so a retry is possible if email fails
        if installment:
            from .models import LeadInstallment
            LeadInstallment.objects.filter(id=installment.id).update(invoice_sent=False)
        else:
            from .models import Lead as LeadModel
            LeadModel.objects.filter(id=lead.id).update(invoice_sent=False)

