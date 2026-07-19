"""
image_service.py
================
Centralised image handling for SmartMart.

Priority order
--------------
1.  CLOUDINARY_URL env var present  →  upload to Cloudinary; store public_id.
2.  Fallback                        →  save to static/uploads/products/ (local/dev).

Public API
----------
    save_product_image(file_storage)  →  str | None   (public_id or filename)
    delete_product_image(identifier)  →  None
    product_image_url(identifier)     →  str           (absolute-ish URL)

Call `product_image_url()` from templates/routes instead of building
`url_for('static', filename='uploads/'+x)` directly.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional

# ── Cloudinary (optional dependency) ─────────────────────────────────────────

def _cloudinary_available() -> bool:
    return bool(os.environ.get("CLOUDINARY_URL"))


def _cloudinary():
    """Lazy-import cloudinary; returns the module or None."""
    if not _cloudinary_available():
        return None
    try:
        import cloudinary                     # noqa: F401
        import cloudinary.uploader            # noqa: F401
        return cloudinary
    except ImportError:
        return None


# ── Allowed extensions ────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Local upload path helper ──────────────────────────────────────────────────

def _local_upload_dir(app=None) -> str:
    if app is not None:
        base = app.static_folder
    else:
        from flask import current_app
        base = current_app.static_folder
    path = os.path.join(base, "uploads", "products")
    os.makedirs(path, exist_ok=True)
    return path


# ── Core API ──────────────────────────────────────────────────────────────────

def save_product_image(file_storage, app=None) -> Optional[str]:
    """
    Upload *file_storage* (a Werkzeug FileStorage object).

    Returns:
        - Cloudinary public_id  (prefixed with "cld:")  when Cloudinary is active.
        - Local filename                                  when using local storage.
        - None                                            on failure / invalid file.
    """
    if not file_storage or file_storage.filename == "":
        return None
    if not _allowed(file_storage.filename):
        return None

    cl = _cloudinary()
    if cl:
        try:
            result = cl.uploader.upload(
                file_storage,
                folder="smartmart/products",
                transformation=[
                    {"width": 800, "height": 800, "crop": "limit", "quality": "auto:good"},
                ],
                resource_type="image",
            )
            return "cld:" + result["public_id"]
        except Exception:
            # Cloudinary failed — seek back so local fallback can read the file
            try:
                file_storage.seek(0)
            except Exception:
                pass

    # Local fallback
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    dest = os.path.join(_local_upload_dir(app), filename)
    file_storage.save(dest)
    return filename


def delete_product_image(identifier: Optional[str], app=None) -> None:
    """Delete image by public_id (Cloudinary) or filename (local)."""
    if not identifier:
        return
    if identifier.startswith("cld:"):
        cl = _cloudinary()
        if cl:
            try:
                cl.uploader.destroy(identifier[4:])
            except Exception:
                pass
        return
    # Local file
    try:
        path = os.path.join(_local_upload_dir(app), identifier)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def product_image_url(identifier: Optional[str], width: int = 400) -> Optional[str]:
    """
    Return a browser-usable image URL ready to drop into <img src="...">.

    * Cloudinary identifiers (prefix "cld:") → optimised CDN URL with resize transform.
    * HTTP/HTTPS URLs (e.g. Pexels direct links) → returned as-is.
    * Local filenames → /static/uploads/products/<filename>  (absolute Flask static path).
    * None / empty string → None  (template should show the 🥜 placeholder).

    This function ALWAYS returns a full, browser-usable URL string or None.
    Templates should NOT call url_for() on the result — just use it directly.
    """
    if not identifier:
        return None

    # ── Cloudinary ────────────────────────────────────────────────────────────
    if identifier.startswith("cld:"):
        cl = _cloudinary()
        if cl:
            try:
                from cloudinary import CloudinaryImage
                return CloudinaryImage(identifier[4:]).build_url(
                    transformation=[
                        {"width": width, "height": width, "crop": "fill",
                         "gravity": "auto", "quality": "auto:good", "fetch_format": "auto"},
                    ]
                )
            except Exception:
                pass
        # CLOUDINARY_URL not set or import failed — image not accessible
        return None

    # ── External HTTP/HTTPS URL (e.g. Pexels) ────────────────────────────────
    if identifier.startswith("http://") or identifier.startswith("https://"):
        return identifier

    # ── Local file — return absolute Flask static URL ─────────────────────────
    # Strip any accidental leading path components for security.
    import os as _os
    filename = _os.path.basename(identifier)
    return f"/static/uploads/products/{filename}"


def is_cloudinary_id(identifier: Optional[str]) -> bool:
    return bool(identifier and identifier.startswith("cld:"))


def product_image_static_path(identifier: Optional[str]) -> Optional[str]:
    """
    Return the Flask static filename to pass to url_for('static', filename=...).
    Returns None if identifier is a Cloudinary ID (use product_image_url instead).
    Returns 'uploads/products/<filename>' for local images.
    """
    if not identifier or identifier.startswith("cld:"):
        return None
    # Strip any leading path components for safety
    import os as _os
    filename = _os.path.basename(identifier)
    return f"uploads/products/{filename}"

