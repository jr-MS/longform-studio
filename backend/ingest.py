import hashlib
import io
from pathlib import Path

import fitz
from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import AppError
from .models import Asset, SourcePage
from .storage import Store, uid

MAX_UPLOAD = 25 * 1024 * 1024
MAX_PIXELS = 24_000_000


def ingest(
    store: Store, project_id: str, name: str, role: str, content: bytes
) -> tuple[Asset, list[SourcePage]]:
    if not content or len(content) > MAX_UPLOAD:
        raise AppError("素材必须非空且不超过 25 MB")
    asset_id = uid()
    pages = []
    outputs: dict[str, bytes] = {}
    if content.startswith(b"%PDF"):
        if role != "manual":
            raise AppError("PDF 请指定为产品说明书")
        try:
            doc = fitz.open(stream=content, filetype="pdf")
        except fitz.FileDataError as e:
            raise AppError("PDF 文件损坏，无法读取") from e
        with doc:
            if doc.is_encrypted or not 1 <= len(doc) <= 24:
                raise AppError("仅支持未加密、1 至 24 页的 PDF")
            for i, page in enumerate(doc):
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(1200 / page.rect.width, 1200 / page.rect.width),
                    alpha=False,
                )
                if pixmap.width * pixmap.height > MAX_PIXELS:
                    raise AppError("PDF 页面比例过大，请先拆分页面")
                filename = f"{asset_id}_page_{i + 1}.png"
                outputs[filename] = pixmap.tobytes("png")
                pages.append(
                    SourcePage(
                        id=f"p_{asset_id}_{i + 1}",
                        asset_id=asset_id,
                        page=i + 1,
                        text=page.get_text()[:25000],
                        preview=filename,
                    )
                )
        original = f"{asset_id}.pdf"
        outputs[original] = content
        asset = Asset(
            id=asset_id,
            name=Path(name).name[:180],
            role="manual",
            file=original,
            preview=pages[0].preview,
            mime="application/pdf",
            sha256=hashlib.sha256(content).hexdigest(),
            page_count=len(pages),
        )
    else:
        if role not in ("product", "person", "reference"):
            raise AppError("图片请选择产品、人像或长图参考角色")
        try:
            with Image.open(io.BytesIO(content)) as raw:
                if raw.width * raw.height > MAX_PIXELS:
                    raise AppError("图片不超过 2400 万像素，请先缩小")
                image = ImageOps.exif_transpose(raw).convert("RGB")
                actual_format = raw.format
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
            raise AppError("仅支持有效的 PNG、JPEG 或 WebP 图片") from e
        if actual_format not in ("PNG", "JPEG", "WEBP"):
            raise AppError("仅支持 PNG、JPEG 或 WebP 图片")
        extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[actual_format]
        original = f"{asset_id}.{extension}"
        outputs[original] = content
        preview = f"{asset_id}_preview.png"
        image.thumbnail((1200, 1800))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        outputs[preview] = buffer.getvalue()
        with Image.open(io.BytesIO(content)) as raw:
            width, height = raw.size
        asset = Asset(
            id=asset_id,
            name=Path(name).name[:180],
            role=role,
            file=original,
            preview=preview,
            mime=f"image/{'jpeg' if extension == 'jpg' else extension}",
            width=width,
            height=height,
            sha256=hashlib.sha256(content).hexdigest(),
        )
    for filename, data in outputs.items():
        store.file(project_id, filename).write_bytes(data)
    return asset, pages
