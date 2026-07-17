from pathlib import Path

from db import connect, save_import
from esocial_parser import parse_esocial_xml
from reports import add_credit_estimate, load_analytic, summarize_by_month_rubric, summarize_by_year
from rules import DEFAULT_RULES_PATH, load_rules


DB_PATH = Path("data/validation_esocial.db")


S1010_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<eSocial>
  <evtTabRubrica Id="ID1010">
    <ideEmpregador>
      <tpInsc>1</tpInsc>
      <nrInsc>83840751</nrInsc>
    </ideEmpregador>
    <infoRubrica>
      <inclusao>
        <ideRubrica>
          <codRubr>9001</codRubr>
          <ideTabRubr>1</ideTabRubr>
          <iniValid>2022-01</iniValid>
        </ideRubrica>
        <dadosRubrica>
          <dscRubr>Aviso previo indenizado</dscRubr>
          <natRubr>6003</natRubr>
          <tpRubr>1</tpRubr>
          <codIncCP>11</codIncCP>
          <codIncIRRF>00</codIncIRRF>
          <codIncFGTS>00</codIncFGTS>
        </dadosRubrica>
      </inclusao>
    </infoRubrica>
  </evtTabRubrica>
</eSocial>
"""


S1200_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<eSocial>
  <evtRemun Id="ID1200">
    <ideEvento>
      <perApur>2022-05</perApur>
    </ideEvento>
    <ideEmpregador>
      <tpInsc>1</tpInsc>
      <nrInsc>83840751</nrInsc>
    </ideEmpregador>
    <ideTrabalhador>
      <cpfTrab>12345678901</cpfTrab>
    </ideTrabalhador>
    <dmDev>
      <ideDmDev>1</ideDmDev>
      <infoPerApur>
        <ideEstabLot>
          <tpInsc>1</tpInsc>
          <nrInsc>83840751000100</nrInsc>
          <codLotacao>001</codLotacao>
          <detVerbas>
            <codRubr>9001</codRubr>
            <ideTabRubr>1</ideTabRubr>
            <qtdRubr>1</qtdRubr>
            <vrRubr>1000.00</vrRubr>
          </detVerbas>
        </ideEstabLot>
      </infoPerApur>
    </dmDev>
  </evtRemun>
</eSocial>
"""


def main() -> None:
    DB_PATH.unlink(missing_ok=True)
    conn = connect(DB_PATH)

    for filename, xml_bytes in [
        ("S-1010-rubrica.xml", S1010_XML),
        ("S-1200-remuneracao.xml", S1200_XML),
    ]:
        parsed = parse_esocial_xml(xml_bytes, filename)
        result = save_import(conn, parsed)
        print(result)

    sample_closure = Path(r"G:\1-STUDIO TAX\Empresas\502-WALPA\2022.05.xml")
    if sample_closure.exists():
        parsed = parse_esocial_xml(sample_closure.read_bytes(), sample_closure.name)
        print(save_import(conn, parsed))

    rules = load_rules(DEFAULT_RULES_PATH)
    analytic = add_credit_estimate(load_analytic(conn, rules), 20.0)
    monthly = summarize_by_month_rubric(analytic)
    annual = summarize_by_year(analytic)

    assert len(analytic) == 1, "A validacao esperava 1 lancamento no analitico."
    row = analytic.iloc[0]
    assert row["status_analise"] == "POTENCIAL_CREDITO_ANALISAR"
    assert float(row["valor_base_questionado"]) == 1000.0
    assert float(row["credito_estimado"]) == 200.0
    assert not monthly.empty
    assert not annual.empty

    print("VALIDACAO_OK")
    print(analytic[["per_apur", "cod_rubr", "dsc_rubr", "vr_rubr", "status_analise", "credito_estimado"]])


if __name__ == "__main__":
    main()
