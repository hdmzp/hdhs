# -*- coding: utf-8 -*-
"""
상품 카테고리 분류 모델 학습 스크립트

엑셀 학습 데이터(브랜드명/판매상품명/상품중분류명 컬럼)로
TF-IDF + 로지스틱회귀 분류 모델을 학습해 category_model.pkl로 저장한다.

== 입력 ==
training_data.xlsx (또는 인자로 경로 지정)
필수 컬럼: 브랜드명, 판매상품명, 상품중분류명
(상품대분류명 컬럼이 있어도 사용하지 않음 - 현재 데이터엔 비어있음)

== 출력 ==
category_model.pkl - categorize.py가 이 파일을 로드해 분류에 사용

== 재학습이 필요한 시점 ==
- 미분류(확신도 낮음)로 분류된 상품이 쌓였을 때, 사람이 라벨을 달아
  학습 데이터에 추가한 뒤 재실행
- 새로운 상품 카테고리가 생겼을 때
- 분류 정확도가 떨어진다고 느껴질 때 (계절 변화로 신상품 패턴이 바뀌는 경우 등)

== 사용법 ==
  pip install pandas scikit-learn joblib openpyxl
  python train_model.py training_data.xlsx
"""

import sys
import pandas as pd
import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report

# 건수가 너무 적어 학습/평가가 불안정한 노이즈성 클래스 (필요시 조정)
MIN_CLASS_COUNT = 10


def load_and_clean(xlsx_path: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path, sheet_name=0)
    required = {"브랜드명", "판매상품명", "상품중분류명"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    df = df.dropna(subset=["판매상품명", "상품중분류명"]).reset_index(drop=True)

    # 건수가 너무 적은 클래스 제거 (학습이 안 되고 평가도 불안정함)
    counts = df["상품중분류명"].value_counts()
    rare = counts[counts < MIN_CLASS_COUNT].index.tolist()
    if rare:
        print(f"건수 부족({MIN_CLASS_COUNT}건 미만)으로 제외된 클래스: {rare}")
        df = df[~df["상품중분류명"].isin(rare)].reset_index(drop=True)

    return df


def train(xlsx_path: str, output_path: str = "category_model.pkl"):
    df = load_and_clean(xlsx_path)
    print(f"학습 데이터: {len(df)}건, 클래스 {df['상품중분류명'].nunique()}개")

    X = df["브랜드명"].astype(str) + " " + df["판매상품명"].astype(str)
    y = df["상품중분류명"]

    # 평가용으로 한 번 분리해서 학습 -> 성능 확인
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.15, random_state=42, stratify=y
    )

    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            analyzer="char_wb",   # 한국어 형태소 분석기 없이도 효과적인 문자 n-gram
            ngram_range=(2, 5),
            max_features=50000,
            sublinear_tf=True,
            min_df=2,
        )),
        ("clf", LogisticRegression(
            max_iter=2000,
            C=15,
            class_weight="balanced",
        )),
    ])

    pipeline.fit(X_train, y_train)
    y_pred = pipeline.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"\n검증 정확도: {acc:.4f}\n")
    print(classification_report(y_test, y_pred))

    # 전체 데이터로 최종 재학습 (배포용 - 평가에 썼던 15%도 학습에 포함시켜 최대 활용)
    pipeline.fit(X, y)
    joblib.dump(pipeline, output_path)
    print(f"\n모델 저장 완료: {output_path}")


if __name__ == "__main__":
    xlsx_path = sys.argv[1] if len(sys.argv) > 1 else "training_data.xlsx"
    train(xlsx_path)
