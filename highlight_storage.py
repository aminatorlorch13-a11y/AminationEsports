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
