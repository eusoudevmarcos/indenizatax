"""Motor simples de classificação das verbas."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional
import json
import re
import unicodedata


DEFAULT_RULES_PATH = Path(__file__).resolve().parent / "rules.json"


def load_rules(path: str | Path = DEFAULT_RULES_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rules(rules: Dict[str, Any], path: str | Path = DEFAULT_RULES_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def normalize_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def has_keyword(description: str, keywords: Iterable[str]) -> bool:
    desc = normalize_text(description)
    return any(normalize_text(k) in desc for k in keywords)


def clean_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def classify_rubric(row: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    nat = clean_field(row.get("nat_rubr"))
    cod_inc_cp = clean_field(row.get("cod_inc_cp"))
    dsc = clean_field(row.get("dsc_rubr"))

    if not nat and not cod_inc_cp and not dsc:
        return {
            "is_candidate_nat": False,
            "is_candidate_keyword": False,
            "is_candidate": False,
            "has_cp_incidence": False,
            "has_suspended_or_process": False,
            "status_analise": "SEM_TABELA_S1010",
            "motivo_analise": "Rubrica encontrada na folha, mas sem cruzamento com S-1010. Importe a tabela de rubricas para identificar descricao, natureza e incidencia.",
            "nature_rule_label": "",
        }

    candidate_natures = rules.get("candidate_natures", {})
    non_incidence_codes = set(str(x) for x in rules.get("non_incidence_cp_codes", []))
    suspended_prefixes = tuple(str(x) for x in rules.get("suspended_or_process_cp_prefixes", []))
    keywords = rules.get("candidate_keywords", [])
    review_keywords = rules.get(
        "review_by_name_keywords",
        [
            "ferias",
            "férias",
            "1/3",
            "terco",
            "terço",
            "maternidade",
            "rescis",
            "inden",
            "multa",
            "reembolso",
            "ressarc",
            "ajuda de custo",
        ],
    )

    is_candidate_nat = nat in candidate_natures
    is_candidate_keyword = has_keyword(dsc, keywords)
    is_review_by_name = has_keyword(dsc, review_keywords)
    is_candidate = is_candidate_nat or is_candidate_keyword

    has_cp_incidence = bool(cod_inc_cp) and cod_inc_cp not in non_incidence_codes and not cod_inc_cp.startswith(suspended_prefixes)
    has_suspended_or_process = bool(cod_inc_cp) and cod_inc_cp.startswith(suspended_prefixes)

    if is_candidate and has_cp_incidence:
        status = "POTENCIAL_CREDITO_ANALISAR"
        reason = "Rubrica candidata e com codIncCP indicando incidência parametrizada."
    elif is_candidate and has_suspended_or_process:
        status = "CANDIDATA_COM_PROCESSO_OU_SUSPENSAO"
        reason = "Rubrica candidata, mas codIncCP indica processo/suspensão/exigibilidade específica."
    elif is_candidate:
        status = "CANDIDATA_SEM_INCIDENCIA_CP"
        reason = "Rubrica candidata, porém sem incidência previdenciária parametrizada pela regra atual."
    elif is_review_by_name and has_cp_incidence:
        status = "REVISAR_NOMENCLATURA_BASE_CP"
        reason = "Nomenclatura sensivel com codIncCP indicando composicao de base. Revisar tese/parametrizacao antes de retificar."
    elif is_review_by_name:
        status = "REVISAR_NOMENCLATURA_SEM_BASE_CP"
        reason = "Nomenclatura sensivel, mas sem incidencia previdenciaria pela parametrizacao atual."
    else:
        status = "FORA_DA_TRIAGEM"
        reason = "Natureza/descrição não está na triagem inicial."

    return {
        "is_candidate_nat": is_candidate_nat,
        "is_candidate_keyword": is_candidate_keyword,
        "is_candidate": is_candidate,
        "is_review_by_name": is_review_by_name,
        "has_cp_incidence": has_cp_incidence,
        "has_suspended_or_process": has_suspended_or_process,
        "status_analise": status,
        "motivo_analise": reason,
        "nature_rule_label": candidate_natures.get(nat, ""),
    }
