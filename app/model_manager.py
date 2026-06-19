# app/model_manager.py

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import os
import torch
import logging
from PIL import Image
from ultralytics import YOLO
from huggingface_hub import snapshot_download
from paddleocr import PaddleOCR
from app.config import Config

logger = logging.getLogger(__name__)


# ---------- OCR‑обёртка на PaddleOCR (детекция + распознавание) ----------
class PaddleOCRWrapper:
    def __init__(self):
        try:
            # Отключаем MKL‑DNN из‑за ошибки совместимости на Windows/CPU
            os.environ['FLAGS_use_mkldnn'] = '0'

            self.reader = PaddleOCR(
                lang='ru',
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_rec_score_thresh=0.0,       # сохраняем всё, фильтровать будем сами
                enable_mkldnn=False,             # явное отключение OneDNN
            )
            logger.info("PaddleOCR (PP-OCRv6) initialized successfully")
        except Exception as e:
            logger.error(f"PaddleOCR init failed: {e}")
            self.reader = None

    def predict(self, image_path):
        """
        Принимает путь к изображению (кроп блока).
        Возвращает список строк, каждая строка – словарь:
            { 'bbox': {'x', 'y', 'width', 'height'}, 'text': str, 'confidence': float }
        """
        if self.reader is None:
            return []
        try:
            result = self.reader.predict(image_path)
            if not result or not isinstance(result, list):
                return []
            res = result[0]

            # Берём отфильтрованные полигоны и тексты
            rec_polys = res.get('rec_polys', [])
            rec_texts = res.get('rec_texts', [])
            rec_scores = res.get('rec_scores', [])

            # Если rec_polys пуст, но есть dt_polys – используем их
            if not rec_polys:
                rec_polys = res.get('dt_polys', [])
                rec_texts = res.get('rec_texts', [])
                rec_scores = res.get('rec_scores', [])

            lines = []
            for poly, text, score in zip(rec_polys, rec_texts, rec_scores):
                if not text.strip():
                    continue
                # Преобразуем полигон в ограничивающий прямоугольник
                x_coords = [p[0] for p in poly]
                y_coords = [p[1] for p in poly]
                x_min, x_max = min(x_coords), max(x_coords)
                y_min, y_max = min(y_coords), max(y_coords)
                bbox = {
                    'x': int(x_min),
                    'y': int(y_min),
                    'width': int(x_max - x_min),
                    'height': int(y_max - y_min)
                }
                lines.append({
                    'bbox': bbox,
                    'text': text,
                    'confidence': round(float(score), 3)
                })
            return lines
        except Exception as e:
            logger.error(f"PaddleOCR predict error on {image_path}: {e}")
            return []


# ---------- Детектор YOLO (без изменений) ----------
class YOLODetector:
    def __init__(self, device='cpu'):
        model_path = os.path.join(os.path.dirname(__file__), '..', 'models', 'yolov8n-doclaynet.pt')
        self.model = YOLO(model_path)
        if device == 'cuda':
            self.model.to('cuda')

    def predict(self, image_path):
        results = self.model(image_path, verbose=False)
        boxes = results[0].boxes
        if boxes is None:
            return []
        class_names = self.model.names
        detections = []
        title_classes = {'Title', 'Section-header'}
        image_classes = {'Picture', 'Table', 'Formula'}
        text_classes = {'Text', 'List-item', 'Caption', 'Footnote', 'Page-header', 'Page-footer'}
        for box in boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = box.conf.item()
            cls_id = int(box.cls.item())
            cls_name = class_names[cls_id]
            if cls_name in title_classes:
                mapped_class = 'title'
            elif cls_name in image_classes:
                mapped_class = 'image'
            elif cls_name in text_classes:
                mapped_class = 'text'
            else:
                continue
            detections.append({
                'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                'class': mapped_class, 'conf': conf
            })
        return detections


# ---------- Детектор строк (оставляем для обратной совместимости, но в OCR не используется) ----------
class LineDetector:
    def __init__(self, device='cpu'):
        self.model = None
        try:
            local_dir = snapshot_download(
                "armvectores/yolov8n_handwritten_text_detection",
                cache_dir=os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
            )
            pt_files = [f for f in os.listdir(local_dir) if f.endswith('.pt')]
            if not pt_files:
                raise FileNotFoundError("No .pt file in downloaded model")
            model_path = os.path.join(local_dir, pt_files[0])
            logger.info(f"Loading line detection model from: {model_path}")
            self.model = YOLO(model_path)
            if device == 'cuda':
                self.model.to('cuda')
        except Exception as e:
            logger.error(f"Line detector load failed: {e}")
            self.model = None

    def detect(self, pil_image):
        if self.model is None:
            return []
        results = self.model(pil_image, verbose=False)
        boxes = results[0].boxes
        lines = []
        if boxes is not None:
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                w, h = x2 - x1, y2 - y1
                if w < 5 or h < 5:
                    continue
                lines.append({
                    'x': int(x1), 'y': int(y1),
                    'width': int(w), 'height': int(h)
                })
        return lines


# ---------- Менеджер моделей ----------
class ModelManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        force_cpu = Config.FORCE_CPU
        self.device = 'cpu' if force_cpu or not torch.cuda.is_available() else 'cuda'
        logger.info(f"Device selected: {self.device}")

        self.detector = YOLODetector(device=self.device)
        self.line_detector = LineDetector(device=self.device)
        self.ocr = self._load_ocr_model()

    def _load_ocr_model(self):
        logger.info("Loading PaddleOCR model...")
        return PaddleOCRWrapper()