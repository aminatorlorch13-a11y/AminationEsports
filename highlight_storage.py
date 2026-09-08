import cloudinary
import cloudinary.uploader

from config import Config


def configure_cloudinary():
    """Configure the Cloudinary SDK from application settings."""
    if not all(
        (
            Config.CLOUDINARY_CLOUD_NAME,
            Config.CLOUDINARY_API_KEY,
            Config.CLOUDINARY_API_SECRET,
        )
    ):
        raise RuntimeError(
            "Cloudinary configuration is incomplete."
        )

    cloudinary.config(
        cloud_name=Config.CLOUDINARY_CLOUD_NAME,
        api_key=Config.CLOUDINARY_API_KEY,
        api_secret=Config.CLOUDINARY_API_SECRET,
        secure=True,
    )


def upload_highlight_video(file):
    """Upload a highlight video to Cloudinary."""
    configure_cloudinary()

    return cloudinary.uploader.upload(
        file,
        resource_type="video",
    )


def delete_highlight_video(public_id):
    """Delete a previously uploaded highlight video from Cloudinary."""
    if not public_id:
        raise ValueError("Cloudinary public ID is required.")

    configure_cloudinary()

    return cloudinary.uploader.destroy(
        public_id,
        resource_type="video",
    )


def validate_highlight_upload(upload_result):
    """Validate Cloudinary metadata for an uploaded highlight video."""
    if not isinstance(upload_result, dict):
        return False, "Invalid storage response."

    if upload_result.get("resource_type") != "video":
        return False, "Uploaded resource is not a video."

    if not upload_result.get("public_id"):
        return False, "Uploaded video has no storage identifier."

    if not upload_result.get("secure_url"):
        return False, "Uploaded video has no secure URL."

    video_format = str(
        upload_result.get("format", "")
    ).lower().lstrip(".")

    allowed_formats = {
        extension.lstrip(".").lower()
        for extension in Config.ALLOWED_HIGHLIGHT_VIDEO_EXTENSIONS
    }

    if video_format not in allowed_formats:
        return False, "Uploaded video format is not supported."

    try:
        duration = float(upload_result.get("duration", 0))
    except (TypeError, ValueError):
        return False, "Uploaded video has invalid duration metadata."

    if duration <= 0:
        return False, "Uploaded video has invalid duration."

    if duration > 180:
        return False, "Uploaded video exceeds the maximum duration."

    return True, None
