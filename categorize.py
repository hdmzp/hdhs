# -*- coding: utf-8 -*-
"""
상품 카테고리 분류기

학습된 모델(category_model.pkl)을 로드해 브랜드+상품명으로 카테고리를 예측한다.
크롤러(hd_scraper.py 등)에서 이 모듈을 import해서 사용한다.

== 사용법 ==
  from categorize import classify

  category = classify("삼성(SAMSUNG)", "삼성 비스포크 김치냉장고 4도어")
  # -> "대형가전"

  category = classify("", "정체불명 신상품")
  # -> "" (확신도가 낮으면 빈 문자열 반환 = 미분류)

== 확신도 임계값 ==
모델이 충분히 확신하지 못하는 경우(기본 0.55 미만) "미분류"로 처리한다.
학습 데이터에 없던 새로운 유형의 상품(예: 항공권처럼 단어 자체가 없는 경우)이
여기 걸리며, 이런 항목들이 쌓이면 학습 데이터에 추가해 재학습하면 된다.

== 모델 재학습 ==
새 학습 데이터(엑셀: 브랜드명/판매상품명/상품중분류명 컬럼)가 생기면
train_model.py를 다시 실행해 category_model.pkl을 교체한다.
"""

import os
import joblib

MODEL_PATH = os.path.join(os.path.dirname(__file__), "category_model.pkl")
CONFIDENCE_THRESHOLD = 0.55

_model = None


def _load_model():
    global _model
    if _model is None:
        _model = joblib.load(MODEL_PATH)
    return _model


def classify(brand: str, product: str, threshold: float = CONFIDENCE_THRESHOLD) -> str:
    """
    브랜드명 + 상품명으로 카테고리(상품중분류명)를 예측.
    확신도가 threshold 미만이면 빈 문자열("") 반환 (미분류).
    """
    model = _load_model()
    text = f"{brand or ''} {product or ''}".strip()
    if not text:
        return ""

    proba = model.predict_proba([text])[0]
    idx = proba.argmax()
    confidence = proba[idx]
    if confidence < threshold:
        return ""
    return model.classes_[idx]


def classify_batch(items: list, threshold: float = CONFIDENCE_THRESHOLD) -> list:
    """
    [(brand, product), ...] 리스트를 한 번에 분류 (개별 호출보다 빠름).
    반환: 카테고리 문자열 리스트 (미분류는 "").
    """
    model = _load_model()
    texts = [f"{b or ''} {p or ''}".strip() for b, p in items]
    proba_matrix = model.predict_proba(texts)
    results = []
    for proba in proba_matrix:
        idx = proba.argmax()
        confidence = proba[idx]
        results.append(model.classes_[idx] if confidence >= threshold else "")
    return results


if __name__ == "__main__":
    # 간단한 동작 확인
    samples = [
        ("삼성(SAMSUNG)", "삼성 비스포크 김치냉장고 4도어"),
        ("닥터린", "닥터린 하이퍼셀 대마종자유 12박스"),
        ("", "정체불명 신상품 XYZ"),
    ]
    for brand, product in samples:
        cat = classify(brand, product)
        print(f"{product[:30]:30s} -> {cat or '(미분류)'}")
