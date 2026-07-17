"""Parser leve para XMLs do eSocial.

Foco do MVP:
- S-1000: dados do empregador/contribuinte
- S-1010: tabela de rubricas
- S-1200/S-1202/S-1207: remuneração/benefícios com itensRemun
- S-1210: pagamentos, quando existirem no pacote importado
- S-2299/S-2399: verbas rescisórias com detVerbas

O parser ignora namespaces para funcionar com XMLs de versões diferentes do eSocial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional
import hashlib
import xml.etree.ElementTree as ET


EVENT_MAP = {
    "evtInfoEmpregador": "S-1000",
    "evtTabRubrica": "S-1010",
    "evtRemun": "S-1200",
    "evtRemunRPPS": "S-1202",
    "evtBenPrRP": "S-1207",
    "evtPgtos": "S-1210",
    "evtFechaEvPer": "S-1299",
    "evtDeslig": "S-2299",
    "evtTSVTermino": "S-2399",
}


@dataclass
class ParsedESocialFile:
    filename: str
    sha256: str
    event_type: str = "DESCONHECIDO"
    company: Dict[str, Any] = field(default_factory=dict)
    rubrics: List[Dict[str, Any]] = field(default_factory=list)
    remuneration_items: List[Dict[str, Any]] = field(default_factory=list)
    payments: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def local_name(tag: str) -> str:
    """Return XML local name without namespace."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def iter_name(node: ET.Element, name: str) -> Iterable[ET.Element]:
    for elem in node.iter():
        if local_name(elem.tag) == name:
            yield elem


def first_desc(node: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    if node is None:
        return None
    return next(iter_name(node, name), None)


def children(node: Optional[ET.Element], name: Optional[str] = None) -> List[ET.Element]:
    if node is None:
        return []
    out: List[ET.Element] = []
    for child in list(node):
        if name is None or local_name(child.tag) == name:
            out.append(child)
    return out


def child_text(node: Optional[ET.Element], name: str) -> Optional[str]:
    for child in children(node, name):
        if child.text is not None:
            text = child.text.strip()
            if text != "":
                return text
    return None


def first_text(node: Optional[ET.Element], name: str) -> Optional[str]:
    found = first_desc(node, name)
    if found is not None and found.text is not None:
        text = found.text.strip()
        if text != "":
            return text
    return None


def parse_decimal(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    text = value.strip().replace(".", "").replace(",", ".") if "," in value else value.strip()
    if text == "":
        return None
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def build_parent_map(root: ET.Element) -> Dict[int, ET.Element]:
    return {id(child): parent for parent in root.iter() for child in list(parent)}


def ancestors(elem: ET.Element, parent_map: Dict[int, ET.Element]) -> Iterable[ET.Element]:
    current = elem
    while id(current) in parent_map:
        current = parent_map[id(current)]
        yield current


def nearest_ancestor(elem: ET.Element, parent_map: Dict[int, ET.Element], name: str) -> Optional[ET.Element]:
    for anc in ancestors(elem, parent_map):
        if local_name(anc.tag) == name:
            return anc
    return None


def nearest_ancestor_text(elem: ET.Element, parent_map: Dict[int, ET.Element], ancestor_name: str, child_name: str) -> Optional[str]:
    anc = nearest_ancestor(elem, parent_map, ancestor_name)
    if anc is None:
        return None
    return first_text(anc, child_name)


def find_event_node(root: ET.Element) -> tuple[str, Optional[ET.Element]]:
    for elem in root.iter():
        tag = local_name(elem.tag)
        if tag in EVENT_MAP and elem.attrib.get("Id"):
            return EVENT_MAP[tag], elem
    return "DESCONHECIDO", None


def extract_company(event_node: Optional[ET.Element], event_type: str) -> Dict[str, Any]:
    company: Dict[str, Any] = {
        "tp_insc": None,
        "nr_insc": None,
        "class_trib": None,
        "ind_des_folha": None,
        "event_type_origin": event_type,
    }
    if event_node is None:
        return company

    ide_emp = first_desc(event_node, "ideEmpregador")
    company["tp_insc"] = first_text(ide_emp, "tpInsc")
    company["nr_insc"] = first_text(ide_emp, "nrInsc")

    info_cadastro = first_desc(event_node, "infoCadastro")
    if info_cadastro is not None:
        company["class_trib"] = first_text(info_cadastro, "classTrib")
        company["ind_des_folha"] = first_text(info_cadastro, "indDesFolha")
    else:
        company["class_trib"] = first_text(event_node, "classTrib")
        company["ind_des_folha"] = first_text(event_node, "indDesFolha")

    return company


def extract_rubrics(event_node: Optional[ET.Element], filename: str) -> List[Dict[str, Any]]:
    if event_node is None:
        return []

    rubrics: List[Dict[str, Any]] = []
    for op in event_node.iter():
        if local_name(op.tag) not in {"inclusao", "alteracao"}:
            continue
        ide = first_desc(op, "ideRubrica")
        dados = first_desc(op, "dadosRubrica")
        if ide is None or dados is None:
            continue
        rubrics.append(
            {
                "operacao": local_name(op.tag),
                "cod_rubr": first_text(ide, "codRubr"),
                "ide_tab_rubr": first_text(ide, "ideTabRubr"),
                "ini_valid": first_text(ide, "iniValid"),
                "fim_valid": first_text(ide, "fimValid"),
                "dsc_rubr": first_text(dados, "dscRubr"),
                "nat_rubr": first_text(dados, "natRubr"),
                "tp_rubr": first_text(dados, "tpRubr"),
                "cod_inc_cp": first_text(dados, "codIncCP"),
                "cod_inc_irrf": first_text(dados, "codIncIRRF"),
                "cod_inc_fgts": first_text(dados, "codIncFGTS"),
                "cod_inc_sind": first_text(dados, "codIncSIND"),
                "observacao": first_text(dados, "observacao"),
                "source_file": filename,
            }
        )
    return rubrics


def derive_period(event_node: ET.Element, item: ET.Element, parent_map: Dict[int, ET.Element]) -> Optional[str]:
    """Get the best period reference for a remuneration item."""
    per_ref = nearest_ancestor_text(item, parent_map, "idePeriodo", "perRef")
    if per_ref:
        return per_ref
    per_apur = first_text(event_node, "perApur")
    if per_apur:
        return per_apur
    # Rescisory events may not have perApur. Use desligamento date as fallback.
    dt_deslig = first_text(event_node, "dtDeslig") or first_text(event_node, "dtTerm")
    if dt_deslig and len(dt_deslig) >= 7:
        return dt_deslig[:7]
    return None


def extract_remuneration_items(event_node: Optional[ET.Element], event_type: str, filename: str) -> List[Dict[str, Any]]:
    if event_node is None:
        return []

    parent_map = build_parent_map(event_node)
    out: List[Dict[str, Any]] = []

    cpf = first_text(event_node, "cpfTrab") or first_text(event_node, "cpfBenef")
    matricula = first_text(event_node, "matricula")
    cod_categ = first_text(event_node, "codCateg")

    item_tags = {"itensRemun", "detVerbas"}
    for item in event_node.iter():
        if local_name(item.tag) not in item_tags:
            continue
        cod_rubr = first_text(item, "codRubr")
        vr_rubr = parse_decimal(first_text(item, "vrRubr"))
        if not cod_rubr and vr_rubr is None:
            continue

        dm_dev = nearest_ancestor_text(item, parent_map, "dmDev", "ideDmDev")
        # Some S-1207 structures do not use dmDev. Keep field nullable.
        per = derive_period(event_node, item, parent_map)
        ano = per[:4] if per and len(per) >= 4 else None
        mes = per[5:7] if per and len(per) >= 7 else None

        estab = nearest_ancestor(item, parent_map, "ideEstabLot") or nearest_ancestor(item, parent_map, "ideEstab")
        lotacao = nearest_ancestor(item, parent_map, "ideEstabLot")

        out.append(
            {
                "event_type": event_type,
                "per_apur": per,
                "ano": ano,
                "mes": mes,
                "cpf_trab": cpf,
                "matricula": matricula,
                "cod_categ": cod_categ,
                "ide_dm_dev": dm_dev,
                "cod_rubr": cod_rubr,
                "ide_tab_rubr": first_text(item, "ideTabRubr"),
                "qtd_rubr": parse_decimal(first_text(item, "qtdRubr")),
                "fator_rubr": parse_decimal(first_text(item, "fatorRubr")),
                "vr_rubr": vr_rubr,
                "ind_apur_ir": first_text(item, "indApurIR"),
                "tp_insc_estab": first_text(estab, "tpInsc") if estab is not None else None,
                "nr_insc_estab": first_text(estab, "nrInsc") if estab is not None else None,
                "cod_lotacao": first_text(lotacao, "codLotacao") if lotacao is not None else None,
                "source_file": filename,
            }
        )
    return out


def extract_payments(event_node: Optional[ET.Element], event_type: str, filename: str) -> List[Dict[str, Any]]:
    if event_node is None or event_type != "S-1210":
        return []

    parent_map = build_parent_map(event_node)
    per_apur = first_text(event_node, "perApur")
    cpf = first_text(event_node, "cpfTrab") or first_text(event_node, "cpfBenef")
    payments: List[Dict[str, Any]] = []

    dets = list(iter_name(event_node, "detPgtoFl"))
    if not dets:
        info = first_desc(event_node, "infoPgto")
        payments.append(
            {
                "per_apur": per_apur,
                "cpf_trab": cpf,
                "ide_dm_dev": None,
                "per_ref": None,
                "dt_pgto": first_text(info, "dtPgto") if info is not None else None,
                "vr_liq": parse_decimal(first_text(info, "vrLiq") if info is not None else None),
                "source_file": filename,
            }
        )
        return payments

    for det in dets:
        info_pgto = nearest_ancestor(det, parent_map, "infoPgto")
        payments.append(
            {
                "per_apur": per_apur,
                "cpf_trab": cpf,
                "ide_dm_dev": first_text(det, "ideDmDev"),
                "per_ref": first_text(det, "perRef"),
                "dt_pgto": first_text(info_pgto, "dtPgto") if info_pgto is not None else None,
                "vr_liq": parse_decimal(first_text(info_pgto, "vrLiq") if info_pgto is not None else None),
                "source_file": filename,
            }
        )
    return payments


def parse_esocial_xml(xml_bytes: bytes, filename: str = "arquivo.xml") -> ParsedESocialFile:
    sha256 = hashlib.sha256(xml_bytes).hexdigest()
    parsed = ParsedESocialFile(filename=filename, sha256=sha256)

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        parsed.warnings.append(f"XML inválido: {exc}")
        return parsed

    event_type, event_node = find_event_node(root)
    parsed.event_type = event_type
    parsed.company = extract_company(event_node, event_type)

    if event_node is None:
        parsed.warnings.append("Nenhum evento do eSocial reconhecido neste XML.")
        return parsed

    if event_type == "S-1299":
        parsed.warnings.append(
            "Arquivo de fechamento S-1299 importado. Ele confirma a competencia, mas nao contem rubricas/valores para analise indenizatoria."
        )
    if event_type == "S-1010":
        parsed.rubrics = extract_rubrics(event_node, filename)
    if event_type in {"S-1200", "S-1202", "S-1207", "S-2299", "S-2399"}:
        parsed.remuneration_items = extract_remuneration_items(event_node, event_type, filename)
    if event_type == "S-1210":
        parsed.payments = extract_payments(event_node, event_type, filename)

    return parsed
