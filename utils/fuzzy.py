"""Допуск опечаток: сравнение слов по биграммам (группам из 2 букв).

Пример: «телеграммм» почти совпадает с «телеграм» — считается за него.
Мера близости — коэффициент Dice по мультимножеству биграмм.
"""
from collections import Counter


def bigrams(word):
    w = (word or "").lower()
    return Counter(w[i:i + 2] for i in range(len(w) - 1))


def dice(a, b):
    """Коэффициент Dice (0..1): 1 = идентично, 0 = нет общих биграмм."""
    A = bigrams(a)
    B = bigrams(b)
    total_a = sum(A.values())
    total_b = sum(B.values())
    if not total_a or not total_b:
        return 0.0
    common = sum((A & B).values())
    return 2.0 * common / (total_a + total_b)


def find_best(word, candidates, threshold=0.55):
    """Лучший кандидат из списка, если его близость >= threshold. Иначе None."""
    best = None
    best_score = 0.0
    for c in candidates:
        s = dice(word, c)
        if s > best_score:
            best = c
            best_score = s
    return (best, best_score) if best_score >= threshold else (None, best_score)
