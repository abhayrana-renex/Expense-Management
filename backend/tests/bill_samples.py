"""Realistic bill texts (as a PDF text layer or OCR would yield them) + expected fields.

All companies, GSTINs and numbers are fictional; GSTINs have valid check digits.
"""

OUR_GSTIN = "08AAACA1234B1ZK"  # placeholder for "our company" (Rajasthan); check digit fixed below

SAMPLES: dict[str, dict] = {
    "gst_tax_invoice_table": {
        "text": """TAX INVOICE
Original for Recipient
PAPERWORKS STATIONERS PRIVATE LIMITED
12, MI Road, Jaipur, Rajasthan 302001
GSTIN: {seller}   PAN: AAACP1234Q
Bill To: Arth Housing Finance Ltd
GSTIN: {buyer}
Invoice No: PWS/2026-27/0458        Invoice Date: 14/10/2026
Place of Supply: Rajasthan (08)
Sr  Description of Goods        HSN    Qty   Rate     Amount
1   A4 Copier Paper 75 GSM      4802   10    250.00   2,500.00
2   Gel Pens (Box of 10)        9608   5     120.00   600.00
Taxable Value   CGST   SGST   Total
3,100.00   279.00   279.00   3,658.00
Amount in words: Rupees Three Thousand Six Hundred Fifty Eight Only
""",
        "seller": ("08", "AAACP1234Q", "1"),
        "buyer": ("08", "AAACA1234B", "1"),
        "expect": {
            "invoice_number": "PWS/2026-27/0458",
            "invoice_date": "2026-10-14",
            "taxable_amount": "3100.00",
            "cgst_amount": "279.00",
            "sgst_amount": "279.00",
            "igst_amount": None,
            "total_amount": "3658.00",
            "vendor_name": "PAPERWORKS STATIONERS PRIVATE LIMITED",
            "category_code": "OFFICE",
        },
    },
    "amazon_style_igst": {
        "text": """Tax Invoice/Bill of Supply/Cash Memo
(Original for Recipient)
Sold By :
Cloudtail Retail Solutions Private Limited
Plot 22, Sector 5, Bengaluru, Karnataka, 560100
PAN No: AAKCC9876R
GST Registration No: {seller}
Order Number: 408-1234567-8901234       Order Date: 10.10.2026
Billing Address :
Arth Housing Finance Ltd, Jaipur
Invoice Number : BLR8-1122334
Invoice Details : KA-BLR8-1034567-2627
Invoice Date : 11.10.2026
Sl. No Description Unit Price Qty Net Amount Tax Rate Tax Type Tax Amount Total Amount
1 Logitech Wireless Mouse M235 | B004IO5BMQ ( HSN:8471 ) ₹1,016.10 1 ₹1,016.10 18% IGST ₹182.90 ₹1,199.00
TOTAL: ₹182.90 ₹1,199.00
Amount in Words: One Thousand One Hundred Ninety-nine only
""",
        "seller": ("29", "AAKCC9876R", "1"),
        "buyer": None,
        "expect": {
            "invoice_number": "BLR8-1122334",
            "invoice_date": "2026-10-11",
            "total_amount": "1199.00",
            "igst_amount": "182.90",
            "taxable_amount": "1016.10",
            "vendor_name": "Cloudtail Retail Solutions Private Limited",
        },
    },
    "hotel_folio": {
        "text": """COMFORT INN HOTELS
Station Road, Ajmer, Rajasthan
GSTIN : {seller}
TAX INVOICE
Guest Name : Mr. R Sharma          Room No : 304
Bill No : CIH/26/10/0912           Bill Date : 15-Oct-2026
Arrival : 13-Oct-2026   Departure : 15-Oct-2026
Description              SAC      Amount
Room Charges (2 nights)  996311   5,000.00
CGST @ 6%                          300.00
SGST @ 6%                          300.00
Grand Total                        5,600.00
""",
        "seller": ("08", "AABCC5678D", "1"),
        "buyer": None,
        "expect": {
            "invoice_number": "CIH/26/10/0912",
            "invoice_date": "2026-10-15",
            "taxable_amount": "5000.00",
            "cgst_amount": "300.00",
            "sgst_amount": "300.00",
            "total_amount": "5600.00",
            "vendor_name": "COMFORT INN HOTELS",
            "category_code": "LODGING",
            "hsn_sac": "996311",
        },
    },
    "restaurant_combined_gst": {
        "text": """SPICE GARDEN RESTAURANT
Near Collectorate, Alwar
GSTIN {seller}
Bill No. 4521    Date 16/10/2026   Table 7
Paneer Tikka        1   280.00
Veg Biryani         2   440.00
Sub Total               720.00
GST @ 5%                 36.00
Round Off                -0.00
Net Amount              756.00
""",
        "seller": ("08", "ABEPS4321K", "1"),
        "buyer": None,
        "expect": {
            "invoice_number": "4521",
            "invoice_date": "2026-10-16",
            "taxable_amount": "720.00",
            "cgst_amount": "18.00",
            "sgst_amount": "18.00",
            "total_amount": "756.00",
            "category_code": "MEALS",
            "vendor_name": "SPICE GARDEN RESTAURANT",
        },
    },
    "cab_receipt": {
        "text": """Uber
Thanks for riding, Rohan
Total ₹ 412.50
October 9, 2026
Trip fare   ₹ 392.86
Subtotal    ₹ 392.86
CGST 2.5%   ₹ 9.82
SGST 2.5%   ₹ 9.82
Invoice Number UBR-IN-2026-883421
Uber India Systems Pvt Ltd
GSTIN {seller}
""",
        "seller": ("27", "AABCU9603R", "1"),
        "buyer": None,
        "expect": {
            "invoice_number": "UBR-IN-2026-883421",
            "invoice_date": "2026-10-09",
            "total_amount": "412.50",
            "taxable_amount": "392.86",
            "cgst_amount": "9.82",
            "sgst_amount": "9.82",
            "category_code": "TRAVEL",
        },
    },
    "telecom_bill": {
        "text": """Bharti Airtel Limited
Postpaid Bill
Bill Number : MH1234567890      Bill Date : 05 Oct 2026
Bill Period : 05 Sep 2026 - 04 Oct 2026
Airtel GSTIN : {seller}
Customer GSTIN : {buyer}
Charges for this month         999.00
IGST @ 18%                     179.82
Amount Payable               1,178.82
Due Date : 20 Oct 2026
""",
        "seller": ("27", "AAACB2894G", "1"),
        "buyer": ("08", "AAACA1234B", "1"),
        "expect": {
            "invoice_number": "MH1234567890",
            "invoice_date": "2026-10-05",
            "total_amount": "1178.82",
            "igst_amount": "179.82",
            "taxable_amount": "999.00",
            "category_code": "TELECOM",
        },
    },
    "airline_gst_invoice": {
        "text": """InterGlobe Aviation Limited (IndiGo)
GST Invoice
GSTIN of Supplier: {seller}
Invoice No.: DL2612345678     Date: 08-Nov-2026
PNR: X7Y8Z9    Flight: 6E 2134  DEL-JAI
Customer GSTIN: {buyer}
Place of Supply: Rajasthan (08)
Description          SAC      Taxable Value   IGST Rate   IGST Amount
Air Travel - Economy 996425   4,200.00        5%          210.00
Airport charges (non-taxable)                              350.00
Total Invoice Value                                       4,760.00
""",
        "seller": ("07", "AABCI2726B", "1"),
        "buyer": ("08", "AAACA1234B", "1"),
        "expect": {
            "invoice_number": "DL2612345678",
            "invoice_date": "2026-11-08",
            "igst_amount": "210.00",
            "total_amount": "4760.00",
            "category_code": "TRAVEL",
        },
    },
    "irctc_eticket": {
        "text": """IRCTC E-Ticket
Electronic Reservation Slip (ERS)
PNR No: 2456789012   Train No./Name: 12015 / AJMER SHATABDI
Date of Journey: 14-Nov-2026   Booking Date: 01-Nov-2026
From: NEW DELHI (NDLS)   To: AJMER JN (AJMER)
Ticket Fare: Rs. 1,185.00
IRCTC Convenience Fee (Incl. of GST): Rs. 35.40
Total Fare (all inclusive): Rs. 1,220.40
""",
        "seller": None,
        "expect": {"total_amount": "1220.40", "category_code": "TRAVEL"},
    },
    "food_delivery": {
        "text": """Order Summary
Spice Junction - Alwar
Order ID: 158234569812   Ordered on 13 Nov 2026, 1:42 PM
Paneer Butter Masala x 1        Rs 260.00
Butter Naan x 4                 Rs 160.00
Item Total                      Rs 420.00
Delivery partner fee            Rs 30.00
Taxes & Charges                 Rs 21.00
Grand Total                     Rs 471.00
""",
        "seller": None,
        "expect": {
            "total_amount": "471.00",
            "invoice_date": "2026-11-13",
            "category_code": "MEALS",
        },
    },
    "fuel_receipt": {
        "text": """INDIAN OIL - SHREE BALAJI FILLING STATION
NH 48, Bhiwadi
Receipt No: 004512    Date: 11/11/2026  Time: 18:22
Product: Petrol
Rate (Rs/Ltr): 104.88
Volume (Ltr): 20.03
Amount: Rs 2,100.75
Mode: UPI
""",
        "seller": None,
        "expect": {
            "invoice_number": "004512",
            "invoice_date": "2026-11-11",
            "total_amount": "2100.75",
            "category_code": "TRAVEL",
        },
    },
    "retail_multi_rate": {
        "text": """SUPERMART RETAIL PVT LTD
GSTIN {seller}
Bill No: SM/26/88421   Date: 2026/11/09
Office tissues            2   180.00
Printer paper A4          1   350.00
GST Breakup:
Rate   Taxable   CGST    SGST
5%     171.43    4.29    4.28
18%    296.61    26.70   26.69
Total  468.04    30.99   30.97
Net Amount   530.00
""",
        "seller": ("08", "AAGCS7766K", "1"),
        "expect": {
            "invoice_number": "SM/26/88421",
            "invoice_date": "2026-11-09",
            "taxable_amount": "468.04",
            "cgst_amount": "30.99",
            "sgst_amount": "30.97",
            "total_amount": "530.00",
        },
    },
    "small_shop_words": {
        "text": """MAHAVEER TRADERS
Kishanpole Bazar, Jaipur
GSTIN: {seller}
Bill No. MT-221            Dt. 07.11.2026
Ceiling fan repair labour           1,000/-
CGST @ 9%                             90/-
SGST @ 9%                             90/-
Total                              1,180/-
Rupees One Thousand One Hundred Eighty Only
""",
        "seller": ("08", "ABCPM5678L", "1"),
        "expect": {
            "invoice_number": "MT-221",
            "invoice_date": "2026-11-07",
            "cgst_amount": "90.00",
            "sgst_amount": "90.00",
            "total_amount": "1180.00",
            "taxable_amount": "1000.00",
            "category_code": "REPAIRS",
        },
    },
    "ocr_noise": {
        "text": """GRAND PALACE HOTEL
GSTIN: {seller}
Invoice No: GPH/1O2   Date: O9/11/2026
Room Tariff             2,5OO.OO
CGST @ 6%                 15O.OO
SGST @ 6%                 15O.OO
Grand Total             2,8OO.OO
""",
        "seller": ("08", "AAFCG4321P", "1"),
        "expect": {
            "total_amount": "2800.00",
            "taxable_amount": "2500.00",
            "cgst_amount": "150.00",
            "category_code": "LODGING",
        },
    },
}


def gstin_for(state: str, pan: str, entity: str) -> str:
    from app.services.bills.gstin import check_char

    first = f"{state}{pan}{entity}Z"
    return first + check_char(first)


def render(name: str) -> tuple[str, dict]:
    s = SAMPLES[name]
    seller = gstin_for(*s["seller"]) if s.get("seller") else ""
    buyer = gstin_for(*s["buyer"]) if s.get("buyer") else ""
    text = s["text"].replace("{seller}", seller).replace("{buyer}", buyer)
    expect = dict(s["expect"])
    if seller:
        expect["vendor_gstin"] = seller
    if buyer:
        expect["buyer_gstin"] = buyer
    return text, expect
