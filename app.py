from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import zipfile

import pandas as pd
import streamlit as st

from db import connect, list_companies, save_import, table_counts, update_company_name
from esocial_parser import parse_esocial_xml
from reports import (
    add_credit_estimate,
    apply_filters,
    build_inss_base_map,
    load_analytic,
    load_imports,
    load_payment_summary,
    load_rubrics,
    summarize_credit_by_month,
    summarize_by_month_rubric,
    summarize_by_year,
    to_excel_bytes,
    to_perdcomp_simple_excel_bytes,
    to_perdcomp_simple_pdf_bytes,
)
from rules import DEFAULT_RULES_PATH, classify_rubric, load_rules, save_rules


APP_TITLE = "IndenizaTax"
APP_SUBTITLE = "Análise eSocial para verbas indenizatórias e créditos PER/DCOMP"
DB_PATH = Path("data/esocial.db")


st.set_page_config(page_title="IndenizaTax | eSocial PER/DCOMP", layout="wide")


def inject_css() -> None:
    st.markdown(
        """
        <style>
            :root {
                --brand-red: #e21b3c;
                --panel-border: rgba(148, 163, 184, 0.22);
                --muted-text: #9ca3af;
            }

            .block-container {
                padding-top: 1.6rem;
                padding-bottom: 3rem;
                max-width: 1480px;
            }

            .app-hero {
                border: 1px solid var(--panel-border);
                border-radius: 8px;
                padding: 22px 24px;
                margin-bottom: 16px;
                background: linear-gradient(135deg, rgba(226, 27, 60, 0.12), rgba(15, 23, 42, 0.03));
            }

            .app-hero h1 {
                margin: 0;
                font-size: 2.2rem;
                line-height: 1.05;
                letter-spacing: 0;
            }

            .app-hero p {
                max-width: 980px;
                margin: 8px 0 0 0;
                color: var(--muted-text);
                font-size: 0.98rem;
            }

            .section-card {
                border: 1px solid var(--panel-border);
                border-radius: 8px;
                padding: 16px 18px;
                margin: 10px 0 16px 0;
                background: rgba(255, 255, 255, 0.02);
            }

            .section-title {
                font-size: 1.06rem;
                font-weight: 700;
                margin-bottom: 4px;
            }

            .section-help {
                color: var(--muted-text);
                font-size: 0.9rem;
                margin-bottom: 8px;
            }

            .stMetric {
                border: 1px solid var(--panel-border);
                border-radius: 8px;
                padding: 14px 16px;
                background: rgba(255, 255, 255, 0.025);
            }

            div[data-testid="stMetricValue"] {
                font-size: 1.45rem;
                white-space: nowrap;
            }

            div[data-testid="stMetricLabel"] {
                color: var(--muted-text);
            }

            div[data-testid="stTabs"] button {
                font-weight: 650;
            }

            div[data-testid="stTabs"] button[aria-selected="true"] {
                color: var(--brand-red);
            }

            div[data-testid="stSidebar"] h3 {
                margin-top: 0.6rem;
            }

            .small-note {
                color: var(--muted-text);
                font-size: 0.84rem;
                margin-top: -2px;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_connection():
    return connect(DB_PATH)


def money(value: float | int | None) -> str:
    value = 0.0 if value is None else float(value)
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_cnpj(value: str | None) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    if len(digits) == 8:
        return f"CNPJ base {digits}"
    return digits or "-"


def format_int(value: int | float | None) -> str:
    return f"{int(value or 0):,}".replace(",", ".")


def iter_uploaded_xmls(uploaded_files):
    for uploaded in uploaded_files or []:
        name = uploaded.name
        data = uploaded.getvalue()
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(BytesIO(data)) as zf:
                    for info in zf.infolist():
                        if info.is_dir() or not info.filename.lower().endswith(".xml"):
                            continue
                        yield f"{name}/{info.filename}", zf.read(info)
            except zipfile.BadZipFile:
                st.error(f"ZIP inválido: {name}")
        else:
            yield name, data


def show_report_actions(df: pd.DataFrame, label: str, filename: str) -> None:
    st.download_button(
        label,
        df.to_csv(index=False, sep=";").encode("utf-8-sig"),
        file_name=filename,
        mime="text/csv",
    )


def money_column_config() -> dict[str, st.column_config.NumberColumn]:
    return {
        "valor_total_folha": st.column_config.NumberColumn("Valor total da folha", format="R$ %.2f"),
        "valor_base_questionada": st.column_config.NumberColumn("Base questionada", format="R$ %.2f"),
        "credito_estimado": st.column_config.NumberColumn("Crédito estimado", format="R$ %.2f"),
        "valor_liquido_pago_s1210": st.column_config.NumberColumn("Líquido pago S-1210", format="R$ %.2f"),
    }


def credit_monthly_view(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["cnpj"] = out["cnpj_completo"].apply(format_cnpj)
    columns = [
        "razao_social",
        "cnpj",
        "ano",
        "mes",
        "periodo_apurado",
        "periodos_pagamento",
        "primeira_data_pagamento",
        "ultima_data_pagamento",
        "status_credito",
        "valor_total_folha",
        "valor_base_questionada",
        "credito_estimado",
        "rubricas",
        "rubricas_com_credito",
        "trabalhadores",
        "lancamentos",
        "valor_liquido_pago_s1210",
        "pagamentos_s1210",
    ]
    return out[[column for column in columns if column in out.columns]]


def get_company_options(companies) -> dict[str, int | None]:
    options: dict[str, int | None] = {"Todas as empresas": None}
    for row in companies:
        company_name = row["razao_social"] or f"Empresa CNPJ base {row['nr_insc']}"
        company_doc = format_cnpj(row["cnpj_completo"] or row["nr_insc"])
        options[f"{company_name} - {company_doc}"] = row["id"]
    return options


inject_css()
conn = get_connection()
rules = load_rules(DEFAULT_RULES_PATH)

if "upload_key" not in st.session_state:
    st.session_state.upload_key = 0
if "processed_batches" not in st.session_state:
    st.session_state.processed_batches = []


st.markdown(
    f"""
    <div class="app-hero">
        <h1>{APP_TITLE}</h1>
        <p>{APP_SUBTITLE}. Importe XMLs ou ZIPs do eSocial, cruze remunerações com S-1010 e gere um dossiê de conferência para revisão fiscal.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


companies = list_companies(conn)
company_options = get_company_options(companies)

st.sidebar.title("Filtros")
selected_company_label = st.sidebar.selectbox("Empresa", list(company_options.keys()))
selected_company_id = company_options[selected_company_label]

st.sidebar.markdown("### Estimativa")
rate = st.sidebar.number_input(
    "Alíquota estimada (%)",
    min_value=0.0,
    max_value=100.0,
    value=float(rules.get("default_estimated_rate_percent", 20.0)),
    step=0.5,
    help="Estimativa para triagem. Ajuste conforme tese, empresa e período, considerando INSS patronal, RAT/FAP e terceiros.",
)

analytic_base = load_analytic(conn, rules, selected_company_id)
periods = sorted([p for p in analytic_base.get("per_apur", pd.Series(dtype=str)).dropna().unique()]) if not analytic_base.empty else []
if periods:
    start_period = st.sidebar.selectbox("Competência inicial", periods, index=0)
    end_period = st.sidebar.selectbox("Competência final", periods, index=len(periods) - 1)
else:
    start_period = None
    end_period = None

status_options = [
    "POTENCIAL_CREDITO_ANALISAR",
    "CANDIDATA_COM_PROCESSO_OU_SUSPENSAO",
    "CANDIDATA_SEM_INCIDENCIA_CP",
    "REVISAR_NOMENCLATURA_BASE_CP",
    "REVISAR_NOMENCLATURA_SEM_BASE_CP",
    "SEM_TABELA_S1010",
    "FORA_DA_TRIAGEM",
]
selected_status = st.sidebar.multiselect("Status da análise", status_options, default=status_options)

analytic_filtered = add_credit_estimate(
    apply_filters(analytic_base, start_period, end_period, selected_status),
    rate,
)
base_map_filtered = build_inss_base_map(analytic_filtered)

st.sidebar.markdown("### Orientação")
st.sidebar.caption(
    "Crédito estimado é indicativo. Use a tela para localizar rubricas e valores; a decisão de retificação precisa de validação fiscal e documental."
)

if analytic_filtered.empty:
    st.info("Nenhum lançamento encontrado para os filtros atuais. Importe XMLs ou ajuste empresa/período/status.")
else:
    s1010_pending = base_map_filtered["valor_sem_s1010"].sum() if not base_map_filtered.empty else 0.0
    base_review = analytic_filtered["valor_base_questionado"].sum()
    c1, c2, c3, c4, c5 = st.columns([0.9, 1.45, 1.25, 1.15, 1.25])
    c1.metric("Lançamentos", format_int(len(analytic_filtered)))
    c2.metric("Total da folha", money(analytic_filtered["vr_rubr"].sum()))
    c3.metric("Base em revisão", money(base_review))
    c4.metric("Pendente S-1010", money(s1010_pending))
    c5.metric("Crédito estimado", money(analytic_filtered["credito_estimado"].sum()))


import_tab, analytic_tab, base_tab, monthly_tab, annual_tab, rubrics_tab, rules_tab = st.tabs(
    ["Importação", "Analítico", "Base INSS", "Mês e rubrica", "Crédito mensal", "Rubricas", "Regras"]
)


with import_tab:
    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">1) Importação</div>
            <div class="section-help">Envie XMLs avulsos ou ZIPs contendo XMLs. Para cruzamento completo, inclua S-1010 e eventos de remuneração/rescisão como S-1200, S-2299 e S-2399.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    counts = table_counts(conn)
    d1, d2, d3, d4, d5 = st.columns(5)
    d1.metric("Empresas", format_int(counts["empresas"]))
    d2.metric("Arquivos", format_int(counts["arquivos_importados"]))
    d3.metric("Rubricas", format_int(counts["rubricas"]))
    d4.metric("Remunerações", format_int(counts["remuneracoes"]))
    d5.metric("Pagamentos", format_int(counts["pagamentos"]))

    if counts["arquivos_importados"] > 0 and counts["remuneracoes"] == 0:
        st.warning(
            "Os arquivos foram registrados, mas ainda não há remunerações na base. "
            "Para as abas analíticas funcionarem, importe eventos de folha como S-1200, S-2299 ou S-2399."
        )

    uploaded_files = st.file_uploader(
        "Arquivos XML ou ZIP",
        type=["xml", "zip"],
        accept_multiple_files=True,
        key=f"xml_uploader_{st.session_state.upload_key}",
    )
    if st.button("Processar arquivos", type="primary", disabled=not uploaded_files):
        results = []
        batch_files = []
        with st.spinner("Processando arquivos do eSocial..."):
            for filename, xml_bytes in iter_uploaded_xmls(uploaded_files):
                parsed = parse_esocial_xml(xml_bytes, filename)
                result = save_import(conn, parsed)
                results.append(result)
                batch_files.append({"filename": filename, "bytes": xml_bytes})
        st.session_state.processed_batches.insert(
            0,
            {
                "processed_at": pd.Timestamp.now().strftime("%d/%m/%Y %H:%M:%S"),
                "files": batch_files,
                "results": results,
            },
        )
        st.session_state.upload_key += 1
        conn = get_connection()
        st.rerun()

    st.markdown("### 2) Arquivos processados")
    if not st.session_state.processed_batches:
        st.info("Nenhum lote processado nesta sessão.")
    else:
        for batch_index, batch in enumerate(st.session_state.processed_batches):
            with st.expander(f"Lote {batch['processed_at']} - {len(batch['results'])} arquivo(s)", expanded=batch_index == 0):
                st.dataframe(pd.DataFrame(batch["results"]), width="stretch")
                if st.button("Processar novamente", key=f"reprocess_{batch_index}"):
                    retry_results = []
                    with st.spinner("Reprocessando lote..."):
                        for item in batch["files"]:
                            parsed = parse_esocial_xml(item["bytes"], item["filename"])
                            retry_results.append(save_import(conn, parsed))
                    batch["processed_at"] = pd.Timestamp.now().strftime("%d/%m/%Y %H:%M:%S")
                    batch["results"] = retry_results
                    st.rerun()

    st.markdown("### 3) Empresas cadastradas")
    companies_df = pd.DataFrame([dict(row) for row in list_companies(conn)])
    if not companies_df.empty:
        companies_df["razao_social"] = companies_df.apply(
            lambda row: row["razao_social"] or f"Empresa CNPJ base {row['nr_insc']}",
            axis=1,
        )
        companies_df["cnpj_formatado"] = companies_df.apply(
            lambda row: format_cnpj(row.get("cnpj_completo") or row.get("nr_insc")),
            axis=1,
        )
    st.dataframe(companies_df, width="stretch")

    if selected_company_id:
        st.markdown("#### Complementar nome da empresa")
        st.caption("Quando o XML não trouxer razão social, preencha aqui para melhorar os relatórios.")
        current_name = ""
        for row in list_companies(conn):
            if row["id"] == selected_company_id:
                current_name = row["razao_social"] or ""
                break
        new_name = st.text_input("Razão social", value=current_name)
        if st.button("Salvar razão social"):
            update_company_name(conn, selected_company_id, new_name)
            st.success("Empresa atualizada.")

    st.markdown("### 4) Histórico de importações")
    st.dataframe(load_imports(conn), width="stretch")


with analytic_tab:
    st.subheader("Relatório analítico")
    if analytic_filtered.empty:
        st.info("Nenhum lançamento encontrado para os filtros atuais.")
    else:
        if analytic_filtered["dsc_rubr"].isna().all():
            st.warning("Os lançamentos foram importados, mas a tabela S-1010 de rubricas não foi localizada para cruzamento. Importe os XMLs S-1010 da empresa/período.")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Lançamentos", format_int(len(analytic_filtered)))
        c2.metric("Valor pago", money(analytic_filtered["vr_rubr"].sum()))
        c3.metric("Base questionada", money(analytic_filtered["valor_base_questionado"].sum()))
        c4.metric("Crédito estimado", money(analytic_filtered["credito_estimado"].sum()))

        st.dataframe(analytic_filtered, width="stretch", height=520)
        show_report_actions(analytic_filtered, "Baixar analítico CSV", "relatorio_analitico_esocial.csv")


with base_tab:
    st.subheader("Mapa da base INSS por rubrica")
    if base_map_filtered.empty:
        st.info("Nenhum lançamento encontrado para montar o mapa da base.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total da folha", money(base_map_filtered["valor_pago"].sum()))
        c2.metric("Em base CP", money(base_map_filtered["valor_em_base_cp"].sum()))
        c3.metric("Pendente S-1010", money(base_map_filtered["valor_sem_s1010"].sum()))
        c4.metric("Crédito estimado", money(base_map_filtered["credito_estimado"].sum()))

        if base_map_filtered["valor_sem_s1010"].sum() > 0:
            st.warning("Existem rubricas da folha sem S-1010. Para provar se entram ou não na base, importe a tabela de rubricas correspondente a esses códigos.")

        review_rows = base_map_filtered[base_map_filtered["credito_estimado"] > 0]
        if not review_rows.empty:
            st.markdown("#### Rubricas com crédito estimado")
            st.dataframe(
                review_rows[
                    [
                        "cod_rubr",
                        "dsc_rubr",
                        "cod_inc_cp",
                        "status_analise",
                        "valor_pago",
                        "valor_base_questionado",
                        "credito_estimado",
                        "origem_credito",
                    ]
                ],
                width="stretch",
                height=260,
            )

        st.markdown("#### Mapa completo")
        st.dataframe(base_map_filtered, width="stretch", height=520)
        show_report_actions(base_map_filtered, "Baixar mapa da base INSS CSV", "mapa_base_inss_por_rubrica.csv")


with monthly_tab:
    st.subheader("Consolidado por competência e rubrica")
    monthly = summarize_by_month_rubric(analytic_filtered)

    if monthly.empty:
        st.info("Nenhum dado consolidado para os filtros atuais.")
    else:
        st.dataframe(monthly, width="stretch", height=520)
        show_report_actions(monthly, "Baixar consolidado mensal CSV", "consolidado_mes_rubrica_esocial.csv")


with annual_tab:
    st.subheader("Crédito mensal por período apurado")
    st.caption(
        "Visão preparada para conferência por competência. O período pago vem dos eventos S-1210 quando eles existirem no pacote importado."
    )
    credit_monthly = summarize_credit_by_month(analytic_filtered)
    payment_summary = load_payment_summary(conn, selected_company_id)
    if not credit_monthly.empty and not payment_summary.empty:
        credit_monthly = credit_monthly.merge(
            payment_summary,
            on=["company_id", "periodo_apurado"],
            how="left",
        )
    elif not credit_monthly.empty:
        credit_monthly["periodos_pagamento"] = None
        credit_monthly["primeira_data_pagamento"] = None
        credit_monthly["ultima_data_pagamento"] = None
        credit_monthly["valor_liquido_pago_s1210"] = 0.0
        credit_monthly["pagamentos_s1210"] = 0

    if credit_monthly.empty:
        st.info("Nenhum crédito mensal encontrado para os filtros atuais.")
    else:
        credit_monthly_display = credit_monthly_view(credit_monthly)
        total_credit = credit_monthly["credito_estimado"].sum()
        total_base = credit_monthly["valor_base_questionada"].sum()
        months_with_credit = int((credit_monthly["credito_estimado"] > 0).sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Períodos apurados", format_int(credit_monthly["periodo_apurado"].nunique()))
        c2.metric("Meses com crédito", format_int(months_with_credit))
        c3.metric("Base questionada", money(total_base))
        c4.metric("Crédito estimado", money(total_credit))

        st.markdown("#### Relatório simplificado para PER/DCOMP")
        st.caption("Arquivo enxuto com empresa, CNPJ, crédito total, ano apurado e crédito mês a mês.")
        report_col1, report_col2 = st.columns([1, 1])
        report_col1.download_button(
            "Baixar Excel simplificado",
            to_perdcomp_simple_excel_bytes(credit_monthly),
            file_name="relatorio_perdcomp_simplificado.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        report_col2.download_button(
            "Baixar PDF simplificado",
            to_perdcomp_simple_pdf_bytes(credit_monthly),
            file_name="relatorio_perdcomp_simplificado.pdf",
            mime="application/pdf",
        )

        st.markdown("#### Resumo geral")
        st.dataframe(credit_monthly_display, width="stretch", height=360, column_config=money_column_config())
        show_report_actions(credit_monthly_display, "Baixar crédito mensal CSV", "credito_mensal_perdcomp.csv")

        st.markdown("#### Separado por ano")
        for year in sorted([year for year in credit_monthly["ano"].dropna().unique()]):
            year_df = credit_monthly[credit_monthly["ano"] == year].copy()
            year_credit = year_df["credito_estimado"].sum()
            year_base = year_df["valor_base_questionada"].sum()
            with st.expander(f"{year} - Crédito estimado {money(year_credit)}", expanded=True):
                y1, y2, y3 = st.columns(3)
                y1.metric("Base questionada", money(year_base))
                y2.metric("Crédito estimado", money(year_credit))
                y3.metric("Períodos", format_int(year_df["periodo_apurado"].nunique()))
                st.dataframe(credit_monthly_view(year_df), width="stretch", height=300, column_config=money_column_config())

    if not analytic_filtered.empty:
        monthly = summarize_by_month_rubric(analytic_filtered)
        annual = summarize_by_year(analytic_filtered)
        rubrics = load_rubrics(conn, selected_company_id)
        excel_bytes = to_excel_bytes(
            {
                "Analitico": analytic_filtered,
                "Base_INSS": base_map_filtered,
                "Credito_Mensal": credit_monthly_view(credit_monthly),
                "Mes_Rubrica": monthly,
                "Ano_Status": annual,
                "Rubricas": rubrics,
            }
        )
        st.download_button(
            "Baixar dossiê Excel completo",
            excel_bytes,
            file_name="dossie_esocial_perdcomp.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


with rubrics_tab:
    st.subheader("Tabela de rubricas importadas")
    rubrics_df = load_rubrics(conn, selected_company_id)
    if rubrics_df.empty:
        st.info("Nenhuma rubrica importada. Importe XMLs S-1010.")
    else:
        class_df = rubrics_df.apply(lambda row: pd.Series(classify_rubric(row.to_dict(), rules)), axis=1)
        rubrics_view = pd.concat([rubrics_df, class_df], axis=1)
        st.dataframe(rubrics_view, width="stretch", height=520)
        show_report_actions(rubrics_view, "Baixar rubricas CSV", "rubricas_esocial.csv")


with rules_tab:
    st.subheader("Regras de triagem")
    st.write(
        "Edite naturezas e palavras-chave conforme a tese adotada, parecer jurídico ou parametrização interna. "
        "A regra padrão apenas separa o que deve ir para análise."
    )
    rules_text = st.text_area("rules.json", value=json.dumps(rules, ensure_ascii=False, indent=2), height=520)
    if st.button("Salvar regras"):
        try:
            new_rules = json.loads(rules_text)
            save_rules(new_rules, DEFAULT_RULES_PATH)
            st.success("Regras salvas. Recarregue a página para aplicar em todos os relatórios.")
        except json.JSONDecodeError as exc:
            st.error(f"JSON inválido: {exc}")
