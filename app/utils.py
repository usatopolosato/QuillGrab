# app/utils.py

import os
import json
import uuid
import zipfile
import shutil
from datetime import datetime

import fitz  # PyMuPDF для работы с PDF
import cv2
import numpy as np
from PIL import Image
from natsort import natsorted
from flask import current_app

PROJECTS_FILE = 'projects.json'

# Максимальные размеры изображений (ширина, высота)
MAX_WIDTH = 1200
MAX_HEIGHT = 1600


def _safe_load_json(filepath, default=None):
    """
    Безопасно загружает JSON, пробуя различные кодировки.
    Возвращает загруженные данные или default (по умолчанию None) при ошибке.
    """
    if not os.path.exists(filepath):
        return default
    encodings = ['utf-8', 'cp1251', 'latin-1']
    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    # Если всё не удалось, читаем с заменой плохих символов и пытаемся распарсить
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
            return json.loads(content)
    except json.JSONDecodeError:
        return default


def _get_projects_file_path():
    storage = current_app.config['STORAGE_PATH']
    os.makedirs(storage, exist_ok=True)
    return os.path.join(storage, PROJECTS_FILE)


def load_projects():
    path = _get_projects_file_path()
    data = _safe_load_json(path)
    return data if data is not None else {}


def save_projects(projects):
    path = _get_projects_file_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(projects, f, indent=2, ensure_ascii=False)


def resize_image_to_fit(image_path, max_width=MAX_WIDTH, max_height=MAX_HEIGHT):
    """Масштабирует изображение, чтобы оно помещалось в max_width × max_height.
    Перезаписывает исходный файл."""
    try:
        img = Image.open(image_path)
        w, h = img.size
        scale = min(max_width / w, max_height / h, 1.0)
        if scale < 1.0:
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.LANCZOS)
            img.save(image_path)
    except Exception as e:
        print(f"Ошибка при масштабировании {image_path}: {e}")


def _save_uploaded_image(file_storage, dest_dir):
    """Сохраняет загруженный файл (FileStorage) в dest_dir, возвращает имя сохранённого файла."""
    original_name = file_storage.filename
    if not original_name:
        return None
    base, ext = os.path.splitext(original_name)
    allowed_ext = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.bmp'}
    ext = ext.lower()
    if ext not in allowed_ext:
        ext = '.png'  # fallback
    dest = os.path.join(dest_dir, f"{base}{ext}")
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, f"{base}_{counter}{ext}")
        counter += 1
    file_storage.save(dest)
    return os.path.basename(dest)


def create_project_from_zip(name, zip_data):
    """Создаёт проект из ZIP-архива с изображениями."""
    storage = current_app.config['STORAGE_PATH']
    project_id = str(uuid.uuid4())
    project_dir = os.path.join(storage, project_id)
    originals_dir = os.path.join(project_dir, 'original')
    tmp_zip = os.path.join(project_dir, 'archive.zip')

    try:
        os.makedirs(originals_dir)

        with open(tmp_zip, 'wb') as f:
            f.write(zip_data)

        image_names = []
        allowed_ext = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.bmp'}

        with zipfile.ZipFile(tmp_zip, 'r') as zf:
            for member in zf.namelist():
                base = os.path.basename(member)
                if base.startswith('.') or base.startswith('__MACOSX'):
                    continue
                ext = os.path.splitext(member)[1].lower()
                if ext not in allowed_ext:
                    continue

                dest = os.path.join(originals_dir, base)
                counter = 1
                while os.path.exists(dest):
                    name_part, ext_part = os.path.splitext(base)
                    dest = os.path.join(originals_dir, f"{name_part}_{counter}{ext_part}")
                    counter += 1

                with zf.open(member) as source, open(dest, 'wb') as target:
                    shutil.copyfileobj(source, target)

                resize_image_to_fit(dest)
                image_names.append(os.path.basename(dest))

        os.remove(tmp_zip)

        if not image_names:
            raise ValueError("В архиве нет поддерживаемых изображений")

        image_names = natsorted(image_names)
        pages_count = len(image_names)

        projects = load_projects()
        projects[project_id] = {
            'name': name,
            'created': datetime.now().isoformat(),
            'pages': pages_count,
            'path': project_dir,
            'images': image_names
        }
        save_projects(projects)

        return project_id, pages_count

    except Exception:
        if os.path.exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
        raise


def create_project_from_pdf(name, pdf_data):
    """Создаёт проект из PDF файла, конвертируя страницы в изображения."""
    storage = current_app.config['STORAGE_PATH']
    project_id = str(uuid.uuid4())
    project_dir = os.path.join(storage, project_id)
    originals_dir = os.path.join(project_dir, 'original')
    os.makedirs(originals_dir, exist_ok=True)

    try:
        pdf_doc = fitz.open(stream=pdf_data, filetype="pdf")
        image_names = []
        for page_num in range(len(pdf_doc)):
            page = pdf_doc.load_page(page_num)
            pix = page.get_pixmap(dpi=200)  # достаточно качественно
            img_filename = f"page_{page_num + 1:03d}.png"
            img_path = os.path.join(originals_dir, img_filename)
            pix.save(img_path)
            resize_image_to_fit(img_path)
            image_names.append(img_filename)

        if not image_names:
            raise ValueError("Не удалось извлечь страницы из PDF")

        pdf_doc.close()
        pages_count = len(image_names)

        projects = load_projects()
        projects[project_id] = {
            'name': name,
            'created': datetime.now().isoformat(),
            'pages': pages_count,
            'path': project_dir,
            'images': image_names
        }
        save_projects(projects)

        return project_id, pages_count

    except Exception:
        if os.path.exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
        raise


def create_project_from_images(name, image_files):
    """Создаёт проект из одного или нескольких загруженных изображений.
    image_files: список объектов FileStorage"""
    storage = current_app.config['STORAGE_PATH']
    project_id = str(uuid.uuid4())
    project_dir = os.path.join(storage, project_id)
    originals_dir = os.path.join(project_dir, 'original')
    os.makedirs(originals_dir, exist_ok=True)

    try:
        image_names = []
        for file in image_files:
            saved_name = _save_uploaded_image(file, originals_dir)
            if saved_name:
                saved_path = os.path.join(originals_dir, saved_name)
                resize_image_to_fit(saved_path)
                image_names.append(saved_name)

        if not image_names:
            raise ValueError("Нет допустимых изображений")

        image_names = natsorted(image_names)
        pages_count = len(image_names)

        projects = load_projects()
        projects[project_id] = {
            'name': name,
            'created': datetime.now().isoformat(),
            'pages': pages_count,
            'path': project_dir,
            'images': image_names
        }
        save_projects(projects)

        return project_id, pages_count

    except Exception:
        if os.path.exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
        raise


# --------------------------------------------------------------
# Старые общие функции (без изменений, кроме удалённой create_project)
# --------------------------------------------------------------
def get_project(project_id):
    return load_projects().get(project_id)


def delete_project(project_id):
    project = get_project(project_id)
    if not project:
        return False
    shutil.rmtree(project['path'], ignore_errors=True)
    projects = load_projects()
    del projects[project_id]
    save_projects(projects)
    return True


def list_projects():
    projects = load_projects()
    result = [
        {
            'id': pid,
            'name': info['name'],
            'created': info['created'],
            'pages': info['pages']
        }
        for pid, info in projects.items()
    ]
    result.sort(key=lambda x: x['created'], reverse=True)
    return result


def detect_page(project_id, page_num, detector):
    """Запускает детекцию, при необходимости сжимая изображение до наших лимитов."""
    proj = get_project(project_id)
    if not proj:
        raise FileNotFoundError("Проект не найден")
    if page_num < 1 or page_num > proj['pages']:
        raise ValueError("Неверный номер страницы")

    image_name = proj['images'][page_num - 1]
    originals_dir = os.path.join(proj['path'], 'original')
    image_path = os.path.join(originals_dir, image_name)
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Изображение {image_name} не найдено")

    # Принудительно приводим к MAX_WIDTH × MAX_HEIGHT (если вдруг после загрузки не обрезалось)
    pil_img = Image.open(image_path)
    w, h = pil_img.size
    scale = min(MAX_WIDTH / w, MAX_HEIGHT / h, 1.0)
    if scale < 1.0:
        new_size = (int(w * scale), int(h * scale))
        pil_img = pil_img.resize(new_size, Image.LANCZOS)
        temp_path = os.path.join(originals_dir, f"_temp_{image_name}")
        pil_img.save(temp_path)
        print(f"[detect] Изображение сжато до {new_size}")
        raw_boxes = detector.predict(temp_path)
        os.remove(temp_path)
    else:
        raw_boxes = detector.predict(image_path)

    print(f"[detect] Найдено объектов: {len(raw_boxes)}")

    detections = []
    for idx, box in enumerate(raw_boxes):
        x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']
        detections.append({
            'id': f"box_{idx}",
            'class': box['class'],
            'x': round(x1),
            'y': round(y1),
            'width': round(x2 - x1),
            'height': round(y2 - y1),
            'confidence': round(box['conf'], 3)
        })

    pages_dir = os.path.join(proj['path'], 'pages', str(page_num))
    os.makedirs(pages_dir, exist_ok=True)
    json_path = os.path.join(pages_dir, 'detection_raw.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(detections, f, indent=2, ensure_ascii=False)

    print(f"[detect] Результат сохранён в {json_path}")
    return detections


def get_detections(project_id, page_num, edited=False):
    proj = get_project(project_id)
    if not proj:
        return None
    pages_dir = os.path.join(proj['path'], 'pages', str(page_num))
    filename = 'detection_edited.json' if edited else 'detection_raw.json'
    json_path = os.path.join(pages_dir, filename)
    return _safe_load_json(json_path)


def save_detections(project_id, page_num, detections, edited=True):
    proj = get_project(project_id)
    if not proj:
        raise FileNotFoundError("Проект не найден")
    pages_dir = os.path.join(proj['path'], 'pages', str(page_num))
    os.makedirs(pages_dir, exist_ok=True)
    json_path = os.path.join(pages_dir, 'detection_edited.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(detections, f, indent=2, ensure_ascii=False)


def ensure_dir(path):
    """Создаёт директорию, если она не существует."""
    os.makedirs(path, exist_ok=True)


def deskew_line(image: Image.Image, bbox: dict) -> Image.Image:
    """
    Вырезает строку по bbox, исправляет наклон (если обнаружен).
    bbox: {'x', 'y', 'width', 'height'}
    Возвращает выпрямленное изображение строки.
    """
    cropped = image.crop((bbox['x'], bbox['y'],
                          bbox['x'] + bbox['width'],
                          bbox['y'] + bbox['height']))
    gray = cv2.cvtColor(np.array(cropped), cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) == 0:
        return cropped
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5:
        return cropped
    (h, w) = cropped.size
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(np.array(cropped), M, (w, h),
                             flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(rotated)


def export_training_data(project_id):
    """
    Экспортирует данные проекта (детекции и OCR) в папку training_data.
    Обновляет JSON файлы dataset.json для detection и ocr.
    Возвращает словарь со статистикой.
    """
    proj = get_project(project_id)
    if not proj:
        raise ValueError("Проект не найден")

    storage = current_app.config['STORAGE_PATH']
    train_path = current_app.config.get('TRAINING_DATA_PATH', os.path.join(storage, 'training_data'))

    detection_train_dir = os.path.join(train_path, 'detection')
    ocr_train_dir = os.path.join(train_path, 'ocr')
    os.makedirs(detection_train_dir, exist_ok=True)
    os.makedirs(ocr_train_dir, exist_ok=True)

    images_detection_dir = os.path.join(detection_train_dir, 'images')
    images_ocr_dir = os.path.join(ocr_train_dir, 'images')
    os.makedirs(images_detection_dir, exist_ok=True)
    os.makedirs(images_ocr_dir, exist_ok=True)

    detection_json_path = os.path.join(detection_train_dir, 'dataset.json')
    ocr_json_path = os.path.join(ocr_train_dir, 'dataset.json')

    # Загружаем существующие датасеты (с безопасным чтением)
    detection_data = _safe_load_json(detection_json_path, default=[])
    if detection_data is None:
        detection_data = []
    ocr_data = _safe_load_json(ocr_json_path, default=[])
    if ocr_data is None:
        ocr_data = []

    detection_image_to_idx = {entry['image']: idx for idx, entry in enumerate(detection_data)}
    ocr_image_to_idx = {entry['image']: idx for idx, entry in enumerate(ocr_data)}

    stats = {'detection_pages': 0, 'ocr_lines': 0}

    for page_num in range(1, proj['pages'] + 1):
        page_dir = os.path.join(proj['path'], 'pages', str(page_num))
        if not os.path.exists(page_dir):
            continue

        # ---------- Детекция ----------
        detection_edited_path = os.path.join(page_dir, 'detection_edited.json')
        detection_raw_path = os.path.join(page_dir, 'detection_raw.json')
        detections = None
        if os.path.exists(detection_edited_path):
            detections = _safe_load_json(detection_edited_path)
        if detections is None and os.path.exists(detection_raw_path):
            detections = _safe_load_json(detection_raw_path)

        if detections is not None and isinstance(detections, list) and len(detections) > 0:
            img_filename = proj['images'][page_num - 1]
            src_img_path = os.path.join(proj['path'], 'original', img_filename)
            if os.path.exists(src_img_path):
                ext = os.path.splitext(img_filename)[1]
                dest_img_name = f"{project_id}_page_{page_num}{ext}"
                dest_img_path = os.path.join(images_detection_dir, dest_img_name)
                shutil.copy2(src_img_path, dest_img_path)
                abs_path = os.path.abspath(dest_img_path)

                if abs_path in detection_image_to_idx:
                    idx = detection_image_to_idx[abs_path]
                    detection_data[idx]['annotations'] = detections
                else:
                    detection_data.append({'image': abs_path, 'annotations': detections})
                    detection_image_to_idx[abs_path] = len(detection_data) - 1
                stats['detection_pages'] += 1

        # ---------- OCR ----------
        ocr_edited_path = os.path.join(page_dir, 'ocr_edited.json')
        ocr_raw_path = os.path.join(page_dir, 'ocr_raw.json')
        ocr_data_page = None
        if os.path.exists(ocr_edited_path):
            ocr_data_page = _safe_load_json(ocr_edited_path)
        if ocr_data_page is None and os.path.exists(ocr_raw_path):
            ocr_data_page = _safe_load_json(ocr_raw_path)

        if ocr_data_page and isinstance(ocr_data_page, dict) and 'boxes' in ocr_data_page:
            for box in ocr_data_page['boxes']:
                box_id = box.get('box_id')
                if not box_id:
                    continue
                for line in box.get('lines', []):
                    line_id = line.get('line_id')
                    if not line_id:
                        continue
                    text = line.get('edited_text')
                    if text is None:
                        text = line.get('text')
                    if not text or text == "[нет текста]":
                        continue

                    line_crop_path = os.path.join(page_dir, 'lines', box_id, f"{line_id}.png")
                    if not os.path.exists(line_crop_path):
                        continue

                    dest_crop_name = f"{project_id}_page_{page_num}_box_{box_id}_line_{line_id}.png"
                    dest_crop_path = os.path.join(images_ocr_dir, dest_crop_name)
                    shutil.copy2(line_crop_path, dest_crop_path)
                    abs_crop_path = os.path.abspath(dest_crop_path)

                    if abs_crop_path in ocr_image_to_idx:
                        idx = ocr_image_to_idx[abs_crop_path]
                        ocr_data[idx]['text'] = text
                    else:
                        ocr_data.append({'image': abs_crop_path, 'text': text})
                        ocr_image_to_idx[abs_crop_path] = len(ocr_data) - 1
                    stats['ocr_lines'] += 1

    # Сохраняем обновлённые датасеты
    with open(detection_json_path, 'w', encoding='utf-8') as f:
        json.dump(detection_data, f, ensure_ascii=False, indent=2)
    with open(ocr_json_path, 'w', encoding='utf-8') as f:
        json.dump(ocr_data, f, ensure_ascii=False, indent=2)

    return stats
