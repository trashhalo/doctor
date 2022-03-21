import os
import shutil
from tempfile import NamedTemporaryFile

import img2pdf
import magic
import pytesseract
import requests
from django.http import FileResponse, HttpResponse, JsonResponse
from PIL import Image
from PyPDF2 import PdfFileReader, PdfFileWriter
from pytesseract import Output
import eyed3

from doctor.forms import AudioForm, DocumentForm, ImagePdfForm
from doctor.lib.utils import (
    cleanup_form,
    make_page_with_text,
    make_png_thumbnail_for_instance,
    make_png_thumbnails,
    strip_metadata_from_path,
)
from doctor.tasks import (
    convert_tiff_to_pdf_bytes,
    convert_to_base64,
    convert_to_mp3,
    download_images,
    extract_from_doc,
    extract_from_docx,
    extract_from_html,
    extract_from_pdf,
    extract_from_txt,
    extract_from_wpd,
    get_page_count,
    make_pdftotext_process,
    rasterize_pdf,
    set_mp3_meta_data,
    strip_metadata_from_bytes,
)


def heartbeat(request):
    """Heartbeat endpoint

    :param request:
    :return:
    """
    return JsonResponse({"success": True, "msg": "Heartbeat detected."})


def extract_pdf(request):
    """"""
    form = DocumentForm(request.GET, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    fp = form.cleaned_data["fp"]
    ocr_available = form.cleaned_data["ocr_available"]
    content, err, returncode, extracted_by_ocr = extract_from_pdf(fp, ocr_available)
    cleanup_form(form)
    return HttpResponse(f"{content}")


def image_to_pdf(request):
    """"""

    form = DocumentForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    image = Image.open(form.cleaned_data["fp"])
    pdf_bytes = convert_tiff_to_pdf_bytes(image)
    cleaned_pdf_bytes = strip_metadata_from_bytes(pdf_bytes)
    with NamedTemporaryFile(suffix=".pdf") as output:
        with open(output.name, "wb") as f:
            f.write(cleaned_pdf_bytes)
        cleanup_form(form)
        return HttpResponse(cleaned_pdf_bytes)


def extract_doc_content(request):
    """Extract txt from different document types.

    :return: The content of a document/error message.
    :type: json object
    """

    form = DocumentForm(request.GET, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    ocr_available = form.cleaned_data["ocr_available"]
    extension = form.cleaned_data["extension"]
    fp = form.cleaned_data["fp"]
    extracted_by_ocr = False
    if extension == "pdf":
        content, err, returncode, extracted_by_ocr = extract_from_pdf(fp, ocr_available)
    elif extension == "doc":
        content, err, returncode = extract_from_doc(fp)
    elif extension == "docx":
        content, err, returncode = extract_from_docx(fp)
    elif extension == "html":
        content, err, returncode = extract_from_html(fp)
    elif extension == "txt":
        content, err, returncode = extract_from_txt(fp)
    elif extension == "wpd":
        content, err, returncode = extract_from_wpd(fp)
    else:
        content = ""
        err = "Unable to extract content due to unknown extension"
        returncode = 1

    # Get page count if you can
    page_count = get_page_count(fp, extension)

    os.remove(form.cleaned_data["fp"])
    return JsonResponse(
        {
            "content": content,
            "err": str(err),
            "extracted_by_ocr": extracted_by_ocr,
            "error_code": str(returncode),
            "page_count": page_count,
            "success": True if returncode == 0 else False,
        }
    )


def make_thumbnail_from_range(request):
    """Can we make a range of thumbnails from a pdf?

    :return: The zip of thumbail images.
    """
    form = DocumentForm(request.GET, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    make_png_thumbnails(
        form.cleaned_data["fp"],
        form.cleaned_data["max_dimension"],
        form.cleaned_data["pages"],
        form.cleaned_data["tmp_dir"],
    )
    with NamedTemporaryFile(delete=True, suffix=".zip") as tmp:
        shutil.make_archive(
            f"{tmp.name[:-4]}", "zip", form.cleaned_data["tmp_dir"].name
        )
        shutil.rmtree(form.cleaned_data["tmp_dir"].name)
        cleanup_form(form)
        return FileResponse(open(tmp.name, "rb"))


def make_png_thumbnail(request):
    """Make a thumbnail of the first page of a PDF and return it.

    :return: A response containing our file and any errors
    :type: HTTPS response
    """
    form = DocumentForm(request.GET, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    thumbnail, _, _ = make_png_thumbnail_for_instance(
        form.cleaned_data["fp"],
        form.cleaned_data["max_dimension"],
    )
    os.remove(form.cleaned_data["fp"])
    return HttpResponse(thumbnail)


def page_count(request):
    """Get page count from PDF

    :return: Page count
    """
    form = DocumentForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    extension = form.cleaned_data["extension"]
    pg_count = get_page_count(form.cleaned_data["fp"], extension)
    cleanup_form(form)
    return HttpResponse(pg_count)


def extract_mime_type(request):
    """Identify the mime type of a document

    :return: Mime type
    """
    form = DocumentForm(request.GET, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    mime = form.cleaned_data["mime"]
    mimetype = magic.from_file(form.cleaned_data["fp"], mime=mime)
    cleanup_form(form)
    return JsonResponse({"mimetype": mimetype})


def pdf_to_text(request):
    """Extract text from text based PDFs immediately.

    :return:
    """
    form = DocumentForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    content, err, _ = make_pdftotext_process(form.cleaned_data["fp"])
    cleanup_form(form)
    return JsonResponse({"content": content, "err": err, "success": True})


def images_to_pdf(request):
    """

    :param request:
    :return:
    """
    form = ImagePdfForm(request.GET)
    if not form.is_valid():
        return JsonResponse({"success": False})
    sorted_urls = form.cleaned_data["sorted_urls"]

    if len(sorted_urls) > 1:
        image_list = download_images(sorted_urls)
        with NamedTemporaryFile(suffix=".pdf") as tmp:
            with open(tmp.name, "wb") as f:
                f.write(img2pdf.convert(image_list))
            cleaned_pdf_bytes = strip_metadata_from_path(tmp.name)
    else:
        tiff_image = Image.open(
            requests.get(sorted_urls[0], stream=True, timeout=60 * 5).raw
        )
        pdf_bytes = convert_tiff_to_pdf_bytes(tiff_image)
        cleaned_pdf_bytes = strip_metadata_from_bytes(pdf_bytes)
    return HttpResponse(cleaned_pdf_bytes, content_type="application/pdf")


def fetch_audio_duration(request):
    """Fetch audio duration from file."""
    form = AudioForm(request.GET, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})

    with NamedTemporaryFile(suffix=".mp3") as tmp:
        with open(tmp.name, "wb") as f:
            for chunk in form.cleaned_data["file"].chunks():
                f.write(chunk)
        mp3_file = eyed3.load(tmp.name)
        return HttpResponse(mp3_file.info.time_secs)


def convert_audio(request):
    """Convert audio file to MP3 and update metadata on mp3.

    :return: Converted audio
    """
    form = AudioForm(request.GET, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    filepath = form.cleaned_data["fp"]
    media_file = form.cleaned_data["file"]
    audio_data = form.cleaned_data["audio_data"]
    convert_to_mp3(filepath, media_file)
    set_mp3_meta_data(audio_data, filepath)
    response = FileResponse(open(filepath, "rb"))
    cleanup_form(form)
    return response


def embed_text(request):
    """Embed text onto an image PDF.

    :return: Embedded PDF
    """
    form = DocumentForm(request.GET, request.FILES)
    if not form.is_valid():
        return JsonResponse({"success": False})
    fp = form.cleaned_data["fp"]
    with NamedTemporaryFile(suffix=".tiff") as destination:
        rasterize_pdf(fp, destination.name)
        data = pytesseract.image_to_data(destination.name, output_type=Output.DICT)
        image = Image.open(destination.name)
        w, h = image.width, image.height
        output = PdfFileWriter()
        existing_pdf = PdfFileReader(open(fp, "rb"))
        for page in range(0, existing_pdf.getNumPages()):
            packet = make_page_with_text(page + 1, data, h, w)
            new_pdf = PdfFileReader(packet)
            page = existing_pdf.getPage(page)
            page.mergePage(new_pdf.getPage(0))
            output.addPage(page)

        with NamedTemporaryFile(suffix=".pdf") as pdf_destination:
            outputStream = open(pdf_destination.name, "wb")
            output.write(outputStream)
            outputStream.close()
            img = open(pdf_destination.name, "rb")
            response = FileResponse(img)
            cleanup_form(form)
            return response