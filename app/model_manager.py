# app/model_manager.py

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import os
import re
import torch
import logging
from PIL import Image
from ultralytics import YOLO
from huggingface_hub import snapshot_download
from paddleocr import PaddleOCR
from app.config import Config

# SymSpell
try:
    from symspellpy import SymSpell, Verbosity
    SYMSPELL_AVAILABLE = True
except ImportError:
    SYMSPELL_AVAILABLE = False
    logging.warning("symspellpy не установлен. Установи: pip install symspellpy")

# LLM
try:
    from llama_cpp import Llama
    LLAMA_AVAILABLE = True
except ImportError:
    LLAMA_AVAILABLE = False
    logging.warning("llama-cpp-python не установлен. Установи: pip install llama-cpp-python")

logger = logging.getLogger(__name__)


# ---------- Утилита: нормализация алфавита (замена межъязыковых омоглифов) ----------
def normalize_script(text: str) -> str:
    """
    Анализирует текст: если русских букв > 60%, заменяет похожие латинские на кириллические,
    иначе заменяет кириллические на латинские (для английского текста).
    """
    if not text:
        return text

    cyrillic_count = sum(1 for ch in text if 'А' <= ch <= 'я' or ch in ('Ё', 'ё'))
    latin_count = sum(1 for ch in text if 'A' <= ch <= 'Z' or 'a' <= ch <= 'z')
    total_letters = cyrillic_count + latin_count
    if total_letters == 0:
        return text

    cyrillic_ratio = cyrillic_count / total_letters

    latin_to_cyrillic = {
        'a': 'а', 'e': 'е', 'o': 'о', 'c': 'с', 'p': 'р', 'x': 'х',
        'y': 'у', 'k': 'к', 'm': 'т', 't': 'т', 'b': 'в', 'h': 'н',
        'A': 'А', 'E': 'Е', 'O': 'О', 'C': 'С', 'P': 'Р', 'X': 'Х',
        'B': 'В', 'H': 'Н', 'K': 'К', 'M': 'М', 'T': 'Т', "Q": 'а', 'g': 'д', 'q': 'д'
    }
    cyrillic_to_latin = {
        'а': 'a', 'е': 'e', 'о': 'o', 'с': 'c', 'р': 'p', 'х': 'x',
        'у': 'y', 'к': 'k', 'м': 'm', 'т': 't', 'в': 'b', 'н': 'h',
        'А': 'A', 'Е': 'E', 'О': 'O', 'С': 'C', 'Р': 'P', 'Х': 'X',
        'В': 'B', 'Н': 'H', 'К': 'K', 'М': 'M', 'Т': 'T', 'ч': 'u', 'Ч': 'U', 'п': 'n', 'л': 'n'
    }

    if cyrillic_ratio > 0.6:
        translation_table = str.maketrans(latin_to_cyrillic)
        return text.translate(translation_table)
    else:
        translation_table = str.maketrans(cyrillic_to_latin)
        return text.translate(translation_table)


# ---------- SymSpell постобработчик ----------
class TextPostProcessor:
    def __init__(self, dict_path=None, max_edit_distance=2, prefix_length=7):
        if dict_path is None:
            dict_path = os.path.join(os.path.dirname(__file__), '..', 'models', '10000-russian-words-cyrillic-only.txt')
        self.max_edit_distance = max_edit_distance
        self.sym_spell = None
        if not SYMSPELL_AVAILABLE:
            logger.error("symspellpy не доступен. Постобработка будет отключена.")
            return
        try:
            self.sym_spell = SymSpell(max_dictionary_edit_distance=max_edit_distance,
                                      prefix_length=prefix_length)
            loaded = self.sym_spell.load_dictionary(dict_path, term_index=0, count_index=1)
            if loaded:
                logger.info(f"SymSpell загрузил {len(self.sym_spell.words)} слов из {dict_path}")
            else:
                logger.error(f"Не удалось загрузить словарь: {dict_path}")
                self.sym_spell = None
        except Exception as e:
            logger.error(f"SymSpell инициализация не удалась: {e}")
            self.sym_spell = None

    def _find_best_match(self, word):
        if not self.sym_spell or not word or len(word) <= 2:
            return word
        try:
            suggestions = self.sym_spell.lookup(word.lower(),
                                                Verbosity.CLOSEST,
                                                max_edit_distance=self.max_edit_distance,
                                                include_unknown=False)
            if suggestions:
                best = suggestions[0].term
                if word[0].isupper():
                    best = best.capitalize()
                return best
        except Exception as e:
            logger.debug(f"SymSpell lookup error for '{word}': {e}")
        return word

    def correct_text(self, text):
        if not text or self.sym_spell is None:
            return text
        def replace_word(match):
            return self._find_best_match(match.group(0))
        pattern = r'[а-яА-ЯёЁ]+'
        return re.sub(pattern, replace_word, text)


# ---------- LLM постобработчик (с жёстким контролем строк) ----------
class LLMPostProcessor:
    def __init__(self, model_path, n_ctx=2048, temperature=0.1, verbose=False):
        self.llm = None
        if not LLAMA_AVAILABLE:
            logger.error("llama-cpp-python не доступен. LLM‑постобработка будет отключена.")
            return
        try:
            self.llm = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                n_threads=os.cpu_count(),
                verbose=verbose
            )
            logger.info(f"LLM загружена: {model_path}")
        except Exception as e:
            logger.error(f"Не удалось загрузить LLM: {e}")
            self.llm = None

    def correct_block_lines(self, lines_texts):
        """
        Принимает список сырых текстов строк блока (после SymSpell).
        Возвращает список исправленных текстов строго той же длины и в том же порядке.
        Если LLM не может вернуть все строки, для недостающих берётся оригинальный текст.
        """
        if not self.llm or not lines_texts:
            return lines_texts

        # Нумеруем строки: [1] текст1 \n [2] текст2 ...
        numbered_lines = []
        for i, text in enumerate(lines_texts, start=1):
            numbered_lines.append(f"[{i}] {text}")
        block_text = "\n".join(numbered_lines)

        # Промпт, жёстко требующий сохранения порядка и количества строк
        system = (
            "Ты — опытный лингвистический корректор текстов после OCR-распознавания рукописных конспектов. "
            "Твоя задача — восстановить связный и осмысленный текст, исправить опечатки и склеить разорванные слова.\n\n"
            "ПРАВИЛА ИСПРАВЛЕНИЯ И УГАДЫВАНИЯ:\n"
            "1. Восстанавливай логику: если слово сильно повреждено, содержит много опечаток или превратилось в 'кашу' из букв, "
            "используй контекст соседних слов и тему конспекта, чтобы УГАДАТЬ и заменить его на наиболее подходящее по смыслу реальное слово.\n"
            "2. Сохраняй стиль: не меняй термины, сокращения (если они понятны) и общий стиль автора.\n"
            "3. Оставляй без изменений только полную 'абракадабру' (набор случайных символов), которую физически невозможно вписать в контекст.\n\n"
            "КРИТИЧЕСКИЕ СТРУКТУРНЫЕ ОГРАНИЧЕНИЯ (ЗАПРЕЩЕНО НАРУШАТЬ):\n"
            "- Верни РОВНО столько же строк, сколько получил на вход.\n"
            "- Строго сохраняй исходный порядок и нумерацию: [1] ..., [2] ..., [3] ... и так далее.\n"
            "- НЕ склеивай несколько строк в одну. НЕ разделяй одну строку на несколько. Каждая строка на входе — это строго одна строка на выходе.\n\n"
            "ПРИМЕРЫ РАБОТЫ (Изучи, как нужно угадывать по контексту):\n"
            "Вход:\n"
            "[1] Теорема Пиф агора: кв абрат гипотенузы\n"
            "[2] равен сумме кв адратов к_а_т_е_т_о_в\n"
            "[3] Рассм. треуг-ник АВС, где угл С = 90 град.\n"
            "[4] склррс лин_ейн_ого ур-ия зависит от d\n"
            "[5] xfg%#@ pd911 (нечитаемый шум)\n"
            "Выход:\n"
            "[1] Теорема Пифагора: квадрат гипотенузы\n"
            "[2] равен сумме квадратов катетов\n"
            "[3] Рассм. треуг-ник АВС, где угл С = 90 град.\n"
            "[4] Скорость линейного ур-ия зависит от d\n"
            "[5] xfg%#@ pd911 (нечитаемый шум)\n\n"
            "Ответ должен содержать ТОЛЬКО финальные строки с номерами, без вводных фраз, приветствий и пояснений."
        )

        user = f"Текст для исправления:\n{block_text}"

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]

        try:
            response = self.llm.create_chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=len(block_text) * 2 + 100,
                stop=["<|im_end|>", "<|endoftext|>"]
            )
            corrected_block = response['choices'][0]['message']['content'].strip()
        except Exception as e:
            logger.error(f"LLM correction failed: {e}")
            return lines_texts  # fallback к SymSpell

        # Парсим ответ: ищем строки вида [номер] текст (нежадный поиск до следующего [номер] или конца)
        pattern = r'\[(\d+)\]\s*(.*?)(?=\[\d+\]|$)'
        matches = re.findall(pattern, corrected_block, re.DOTALL)
        corrected_dict = {}
        for num_str, text in matches:
            num = int(num_str)
            if 1 <= num <= len(lines_texts):  # игнорируем номера вне допустимого диапазона
                corrected_dict[num] = text.strip()

        # Собираем итоговый список: если для номера есть исправление — берём его, иначе берём исходный SymSpell текст
        result = []
        for i, original_text in enumerate(lines_texts, start=1):
            if i in corrected_dict:
                result.append(corrected_dict[i])
            else:
                logger.warning(f"LLM не вернула строку {i}, используется оригинал")
                result.append(original_text)
        return result


# ---------- PaddleOCRWrapper ----------
class PaddleOCRWrapper:
    def __init__(self):
        try:
            os.environ['FLAGS_use_mkldnn'] = '0'
            self.reader = PaddleOCR(
                lang='ru',
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_rec_score_thresh=0.0,
                enable_mkldnn=False,
            )
            logger.info("PaddleOCR initialized successfully")
        except Exception as e:
            logger.error(f"PaddleOCR init failed: {e}")
            self.reader = None

    def predict(self, image_path):
        if self.reader is None:
            return []
        try:
            result = self.reader.predict(image_path)
            if not result or not isinstance(result, list):
                return []
            res = result[0]
            rec_polys = res.get('rec_polys', [])
            rec_texts = res.get('rec_texts', [])
            rec_scores = res.get('rec_scores', [])
            if not rec_polys:
                rec_polys = res.get('dt_polys', [])
                rec_texts = res.get('rec_texts', [])
                rec_scores = res.get('rec_scores', [])
            lines = []
            for poly, text, score in zip(rec_polys, rec_texts, rec_scores):
                if not text.strip():
                    continue
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


# ---------- YOLODetector ----------
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


# ---------- LineDetector (не используется, но сохранён) ----------
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


# ---------- ModelManager ----------
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

        # Постобработчики
        self.spell_checker = TextPostProcessor()
        llm_model_path = os.path.join(os.path.dirname(__file__), '..', 'models',
                                      'qwen2.5-1.5b-instruct-q4_k_m.gguf')
        self.llm = LLMPostProcessor(llm_model_path)

    def _load_ocr_model(self):
        logger.info("Loading PaddleOCR model...")
        return PaddleOCRWrapper()

    def preprocess_text(self, text: str) -> str:
        """Нормализация алфавита + исправление через SymSpell."""
        if not text:
            return text
        normalized = normalize_script(text)
        return self.spell_checker.correct_text(normalized)