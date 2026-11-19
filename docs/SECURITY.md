# Security notes: file uploads and e-mailed bills

For VAPT and security review. This document covers every way a file enters the portal:
- **bills:** `POST /api/bills/read` and `POST /api/expenses/{id}/items/{item}/documents`;
- **e-mails:** `POST /api/bills/read-email`;
- **approval e-mails and contracts:** `POST /api/bills/read?read=false`.

Each control has a test in `backend/tests/test_email_bills.py` or `test_bill_reader.py`.

## What a person can upload

| Upload | Accepted (by **content**, extension must match) | Max size |
|---|---|---|
| Bill | PDF, JPG, PNG | `MAX_UPLOAD_SIZE_MB` (10) |
| Bill e-mail | Outlook `.msg` (OLE, must be an Outlook message), saved `.eml` (RFC 822) | `MAX_EMAIL_SIZE_MB` (25) |
| Approval e-mail / contract (`read=false`) | the above | as above |

Anything else is refused (`422 INVALID_FILE`).

## Controls

| Threat | Control | Test |
|---|---|---|
| Type spoofing (`.exe` renamed `.pdf`, `.eml` renamed `.msg`, Word `.doc` renamed `.msg`) | The type is taken from magic bytes / headers, never the name or the browser's `Content-Type`. The extension must match the content. An OLE file must contain Outlook message streams. | `test_type_and_extension_must_match`, `test_word_document_renamed_to_msg_is_refused`, `test_exe_disguised_as_pdf_is_skipped` |
| Malicious PDF (JavaScript, launch actions, embedded files) | PDFs containing `/JavaScript`, `/JS`, `/Launch`, `/EmbeddedFile(s)` or `/RichMedia` are refused. This includes hex-escaped names (`/J#61vaScript`) and markers inside the parsed catalog (`Names`, `OpenAction`). | `test_pdf_active_content_detection_sees_hex_escaped_names`, `test_bill_upload_refuses_pdf_with_javascript`, `test_pdf_with_javascript_in_email_is_skipped_not_stored` |
| HTML e-mail: script, tracking pixels, `javascript:` links | HTML is **never rendered**. The standard-library `HTMLParser` reduces it to text; `script`, `style`, `head` and `template` content is dropped. No URL is ever fetched. The "e-mail as bill" PDF is plain text drawn by reportlab. | `test_eml_without_attachment_reads_the_email_text`, `test_html_to_text_drops_script_and_style` |
| Macros / Office exploits | Word, Excel and other Office attachments are **not opened**. They are listed back as "save it as PDF". No RTF or Office parser is used. | `test_outlook_msg_attachments_become_bills` |
| Nested e-mails / recursion bombs | E-mails attached inside an e-mail are **not opened** (depth 1). They are listed as skipped. | same, `test_eml_with_pdf_attachment_and_nested_email` |
| Resource exhaustion | Limits: e-mail size, OLE entries (2,000), MIME parts (300), attachments examined (40), bills read per e-mail (`EMAIL_MAX_BILLS` 10), body text (20,000 chars), pages read (`BILL_READER_MAX_PAGES`). The request body is read with a byte cap. | `test_email_size_limit` |
| Upload flooding (OCR is CPU-heavy) | `BILL_READ_RATE_LIMIT_PER_MINUTE` (40) per person, on both upload endpoints. Returns `429 RATE_LIMITED`. | `test_upload_rate_limit` |
| Path traversal / filename spoofing (`..\`, `/`, RTL override U+202E, control characters) | Names are display-only: last path part, NFKC, control and bidi characters removed, 120 characters max. Storage keys are server-generated. Download headers use an `[A-Za-z0-9._- ]` filename. | `test_safe_name_strips_paths_controls_and_bidi`, `test_eml_with_pdf_attachment_and_nested_email` |
| Stored XSS via uploaded files | Only PDF / JPG / PNG are ever served inline (signed link, `frame-ancestors` limited to the portal). E-mails and anything else are always `application/octet-stream` + `Content-Disposition: attachment`. All files get `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. The UI never puts an e-mail in the preview frame. | `test_original_email_is_only_ever_a_download` |
| IDOR on staged uploads | A staged (not yet attached) file is visible only to its uploader. Attaching checks the uploader, state and kind. Out-of-scope documents return 404. | `test_unrelated_user_cannot_see_the_staged_email`, `test_create_expense_from_read_bill_with_human_review` |
| Repudiation | `EMAIL_UPLOADED` (sha256, kind, size) is written in the same transaction as the stored original. `EMAIL_IMPORTED` records counts. Each bill gets `BILL_UPLOADED_AND_READ`, and attaching to a claim gets `BILL_ATTACHED` (read vs entered). No e-mail subject or body goes to the application logs. | `test_outlook_msg_attachments_become_bills` |
| Signed download links | HMAC-signed, `SIGNED_URL_TTL_SECONDS` (60 s), bound to the user. Access is re-checked when the link is used. Bearer tokens are never put in URLs. | existing auth tests |

## Libraries used for untrusted files

- **`olefile` 0.47** (pure Python, no dependencies) for the `.msg` container. The portal reads
  only the streams it needs (MS-OXMSG property streams), each with a size cap.
- **Python's `email` package** (`policy.default`) for `.eml`.
- **`pdfplumber` / `pypdfium2`** for reading PDF text, and **RapidOCR** for scans.
- **`reportlab`** only *writes* the plain-text PDF of an e-mail body.

## Known gaps (to close before production)

1. **Antivirus is a hook, not wired.** `scan_for_malware()` in `app/services/storage.py` always
   passes. Connect ClamAV (clamd `INSTREAM`) or the corporate ICAP server. It is already called
   for every bill, e-mail and attachment.
2. **The rate limiter is in-process.** With several API workers, use a shared store (Redis) so the
   per-person limit holds across workers.
3. **Run the API as a low-privilege service account** with the upload directory outside the web
   root (already the case: files are served only through the API).
4. **Outlook on the web / new Outlook** cannot drag a message as a file. Users choose
   *Download* (`.eml`) and drop that. Classic Outlook drag-and-drop gives a `.msg`.
