"""Persistência SQLite do MVP."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import sqlite3

from esocial_parser import ParsedESocialFile


def _translate_sql(sql: str) -> str:
    return sql.replace("?", "%s")


class _PostgresDirectCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def description(self):
        return self._cursor.description

    def _row_to_dict(self, row):
        if row is None:
            return None
        columns = [col.name for col in self._cursor.description]
        return {columns[index]: value for index, value in enumerate(row)}

    def fetchone(self):
        return self._row_to_dict(self._cursor.fetchone())

    def fetchall(self):
        return [self._row_to_dict(row) for row in self._cursor.fetchall()]


class _PostgresPandasCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def description(self):
        if self._cursor.description is None:
            return None
        return [(col.name, None, None, None, None, None, None) for col in self._cursor.description]

    def execute(self, sql: str, params=None):
        self._cursor.execute(_translate_sql(sql), params or ())
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        self._cursor.close()


class PostgresConnection:
    is_postgres = True

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql: str, params=None):
        cursor = self.raw.cursor()
        cursor.execute(_translate_sql(sql), params or ())
        return _PostgresDirectCursor(cursor)

    def cursor(self):
        return _PostgresPandasCursor(self.raw.cursor())

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            sql = statement.strip()
            if not sql or sql.upper().startswith("PRAGMA "):
                continue
            self.execute(sql)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()


def connect(db_path: str | Path = "data/esocial.db"):
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        import psycopg

        raw = psycopg.connect(database_url)
        conn = PostgresConnection(raw)
        init_db(conn)
        return conn

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _is_postgres(conn) -> bool:
    return bool(getattr(conn, "is_postgres", False))


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    if _is_postgres(conn):
        row = conn.execute(
            """
            SELECT 1 AS exists
            FROM information_schema.columns
            WHERE table_name = ? AND column_name = ?
            """,
            (table_name, column_name),
        ).fetchone()
        return row is not None
    existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    return column_name in existing_columns


def init_db(conn) -> None:
    if _is_postgres(conn):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS empresas (
                id SERIAL PRIMARY KEY,
                tp_insc TEXT,
                nr_insc TEXT,
                cnpj_completo TEXT,
                razao_social TEXT,
                class_trib TEXT,
                ind_des_folha TEXT,
                event_type_origin TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(tp_insc, nr_insc)
            );

            CREATE TABLE IF NOT EXISTS arquivos_importados (
                id SERIAL PRIMARY KEY,
                sha256 TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                event_type TEXT,
                company_id INTEGER,
                qtd_rubricas INTEGER DEFAULT 0,
                qtd_remuneracoes INTEGER DEFAULT 0,
                qtd_pagamentos INTEGER DEFAULT 0,
                warnings TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES empresas(id)
            );

            CREATE TABLE IF NOT EXISTS rubricas (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                operacao TEXT,
                cod_rubr TEXT,
                ide_tab_rubr TEXT,
                ini_valid TEXT,
                fim_valid TEXT,
                dsc_rubr TEXT,
                nat_rubr TEXT,
                tp_rubr TEXT,
                cod_inc_cp TEXT,
                cod_inc_irrf TEXT,
                cod_inc_fgts TEXT,
                cod_inc_sind TEXT,
                observacao TEXT,
                source_file TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES empresas(id),
                UNIQUE(company_id, cod_rubr, ide_tab_rubr, ini_valid)
            );

            CREATE TABLE IF NOT EXISTS remuneracoes (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                event_type TEXT,
                per_apur TEXT,
                ano TEXT,
                mes TEXT,
                cpf_trab TEXT,
                matricula TEXT,
                cod_categ TEXT,
                ide_dm_dev TEXT,
                cod_rubr TEXT,
                ide_tab_rubr TEXT,
                qtd_rubr REAL,
                fator_rubr REAL,
                vr_rubr REAL,
                ind_apur_ir TEXT,
                tp_insc_estab TEXT,
                nr_insc_estab TEXT,
                cod_lotacao TEXT,
                source_file TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES empresas(id)
            );

            CREATE TABLE IF NOT EXISTS pagamentos (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                per_apur TEXT,
                cpf_trab TEXT,
                ide_dm_dev TEXT,
                per_ref TEXT,
                dt_pgto TEXT,
                vr_liq REAL,
                source_file TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES empresas(id)
            );
            """
        )
        if not _column_exists(conn, "empresas", "cnpj_completo"):
            conn.execute("ALTER TABLE empresas ADD COLUMN cnpj_completo TEXT")
        conn.commit()
        return

    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS empresas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tp_insc TEXT,
            nr_insc TEXT,
            cnpj_completo TEXT,
            razao_social TEXT,
            class_trib TEXT,
            ind_des_folha TEXT,
            event_type_origin TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(tp_insc, nr_insc)
        );

        CREATE TABLE IF NOT EXISTS arquivos_importados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sha256 TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            event_type TEXT,
            company_id INTEGER,
            qtd_rubricas INTEGER DEFAULT 0,
            qtd_remuneracoes INTEGER DEFAULT 0,
            qtd_pagamentos INTEGER DEFAULT 0,
            warnings TEXT,
            imported_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES empresas(id)
        );

        CREATE TABLE IF NOT EXISTS rubricas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            operacao TEXT,
            cod_rubr TEXT,
            ide_tab_rubr TEXT,
            ini_valid TEXT,
            fim_valid TEXT,
            dsc_rubr TEXT,
            nat_rubr TEXT,
            tp_rubr TEXT,
            cod_inc_cp TEXT,
            cod_inc_irrf TEXT,
            cod_inc_fgts TEXT,
            cod_inc_sind TEXT,
            observacao TEXT,
            source_file TEXT,
            imported_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES empresas(id),
            UNIQUE(company_id, cod_rubr, ide_tab_rubr, ini_valid)
        );

        CREATE TABLE IF NOT EXISTS remuneracoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            event_type TEXT,
            per_apur TEXT,
            ano TEXT,
            mes TEXT,
            cpf_trab TEXT,
            matricula TEXT,
            cod_categ TEXT,
            ide_dm_dev TEXT,
            cod_rubr TEXT,
            ide_tab_rubr TEXT,
            qtd_rubr REAL,
            fator_rubr REAL,
            vr_rubr REAL,
            ind_apur_ir TEXT,
            tp_insc_estab TEXT,
            nr_insc_estab TEXT,
            cod_lotacao TEXT,
            source_file TEXT,
            imported_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES empresas(id)
        );

        CREATE TABLE IF NOT EXISTS pagamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            per_apur TEXT,
            cpf_trab TEXT,
            ide_dm_dev TEXT,
            per_ref TEXT,
            dt_pgto TEXT,
            vr_liq REAL,
            source_file TEXT,
            imported_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES empresas(id)
        );
        """
    )
    if not _column_exists(conn, "empresas", "cnpj_completo"):
        conn.execute("ALTER TABLE empresas ADD COLUMN cnpj_completo TEXT")
    conn.commit()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_or_create_company(conn: sqlite3.Connection, company: Dict[str, Any]) -> Optional[int]:
    tp_insc = company.get("tp_insc")
    nr_insc = company.get("nr_insc")
    if not tp_insc or not nr_insc:
        return None

    now = _now()
    conn.execute(
        """
        INSERT INTO empresas (tp_insc, nr_insc, razao_social, class_trib, ind_des_folha, event_type_origin, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tp_insc, nr_insc) DO UPDATE SET
            razao_social = COALESCE(empresas.razao_social, excluded.razao_social),
            class_trib = COALESCE(excluded.class_trib, empresas.class_trib),
            ind_des_folha = COALESCE(excluded.ind_des_folha, empresas.ind_des_folha),
            event_type_origin = COALESCE(excluded.event_type_origin, empresas.event_type_origin),
            updated_at = excluded.updated_at
        """,
        (
            tp_insc,
            nr_insc,
            company.get("razao_social") or f"Empresa CNPJ base {nr_insc}",
            company.get("class_trib"),
            company.get("ind_des_folha"),
            company.get("event_type_origin"),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM empresas WHERE tp_insc = ? AND nr_insc = ?", (tp_insc, nr_insc)).fetchone()
    return int(row["id"]) if row else None


def update_company_cnpj_from_items(conn: sqlite3.Connection, company_id: int, items: Iterable[Dict[str, Any]]) -> None:
    for item in items:
        cnpj = (item.get("nr_insc_estab") or "").strip()
        if len(cnpj) == 14 and cnpj.isdigit():
            conn.execute(
                """
                UPDATE empresas
                   SET cnpj_completo = COALESCE(cnpj_completo, ?),
                       updated_at = ?
                 WHERE id = ?
                """,
                (cnpj, _now(), company_id),
            )
            return


def already_imported(conn: sqlite3.Connection, sha256: str) -> bool:
    row = conn.execute("SELECT id FROM arquivos_importados WHERE sha256 = ?", (sha256,)).fetchone()
    return row is not None


def table_counts(conn) -> Dict[str, int]:
    tables = ["empresas", "arquivos_importados", "rubricas", "remuneracoes", "pagamentos"]
    counts: Dict[str, int] = {}
    for table in tables:
        row = conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
        counts[table] = int(row["total"] or 0) if row else 0
    return counts


def save_import(conn: sqlite3.Connection, parsed: ParsedESocialFile) -> Dict[str, Any]:
    if already_imported(conn, parsed.sha256):
        return {
            "filename": parsed.filename,
            "event_type": parsed.event_type,
            "status": "ignorado",
            "message": "Arquivo já importado anteriormente (mesmo SHA-256).",
        }

    company_id = get_or_create_company(conn, parsed.company)
    if company_id is None:
        # In rare cases, the XML may not include ideEmpregador. Keep an import record impossible? Better return warning.
        return {
            "filename": parsed.filename,
            "event_type": parsed.event_type,
            "status": "erro",
            "message": "Não foi possível identificar ideEmpregador/tpInsc/nrInsc no XML.",
            "warnings": parsed.warnings,
        }

    now = _now()

    for rub in parsed.rubrics:
        conn.execute(
            """
            INSERT INTO rubricas (
                company_id, operacao, cod_rubr, ide_tab_rubr, ini_valid, fim_valid, dsc_rubr,
                nat_rubr, tp_rubr, cod_inc_cp, cod_inc_irrf, cod_inc_fgts, cod_inc_sind,
                observacao, source_file, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, cod_rubr, ide_tab_rubr, ini_valid) DO UPDATE SET
                operacao = excluded.operacao,
                fim_valid = excluded.fim_valid,
                dsc_rubr = excluded.dsc_rubr,
                nat_rubr = excluded.nat_rubr,
                tp_rubr = excluded.tp_rubr,
                cod_inc_cp = excluded.cod_inc_cp,
                cod_inc_irrf = excluded.cod_inc_irrf,
                cod_inc_fgts = excluded.cod_inc_fgts,
                cod_inc_sind = excluded.cod_inc_sind,
                observacao = excluded.observacao,
                source_file = excluded.source_file,
                imported_at = excluded.imported_at
            """,
            (
                company_id,
                rub.get("operacao"),
                rub.get("cod_rubr"),
                rub.get("ide_tab_rubr"),
                rub.get("ini_valid"),
                rub.get("fim_valid"),
                rub.get("dsc_rubr"),
                rub.get("nat_rubr"),
                rub.get("tp_rubr"),
                rub.get("cod_inc_cp"),
                rub.get("cod_inc_irrf"),
                rub.get("cod_inc_fgts"),
                rub.get("cod_inc_sind"),
                rub.get("observacao"),
                rub.get("source_file"),
                now,
            ),
        )

    for item in parsed.remuneration_items:
        conn.execute(
            """
            INSERT INTO remuneracoes (
                company_id, event_type, per_apur, ano, mes, cpf_trab, matricula, cod_categ,
                ide_dm_dev, cod_rubr, ide_tab_rubr, qtd_rubr, fator_rubr, vr_rubr, ind_apur_ir,
                tp_insc_estab, nr_insc_estab, cod_lotacao, source_file, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                item.get("event_type"),
                item.get("per_apur"),
                item.get("ano"),
                item.get("mes"),
                item.get("cpf_trab"),
                item.get("matricula"),
                item.get("cod_categ"),
                item.get("ide_dm_dev"),
                item.get("cod_rubr"),
                item.get("ide_tab_rubr"),
                item.get("qtd_rubr"),
                item.get("fator_rubr"),
                item.get("vr_rubr"),
                item.get("ind_apur_ir"),
                item.get("tp_insc_estab"),
                item.get("nr_insc_estab"),
                item.get("cod_lotacao"),
                item.get("source_file"),
                now,
            ),
        )

    for pay in parsed.payments:
        conn.execute(
            """
            INSERT INTO pagamentos (
                company_id, per_apur, cpf_trab, ide_dm_dev, per_ref, dt_pgto, vr_liq, source_file, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                pay.get("per_apur"),
                pay.get("cpf_trab"),
                pay.get("ide_dm_dev"),
                pay.get("per_ref"),
                pay.get("dt_pgto"),
                pay.get("vr_liq"),
                pay.get("source_file"),
                now,
            ),
        )

    update_company_cnpj_from_items(conn, company_id, parsed.remuneration_items)

    conn.execute(
        """
        INSERT INTO arquivos_importados (
            sha256, filename, event_type, company_id, qtd_rubricas, qtd_remuneracoes,
            qtd_pagamentos, warnings, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parsed.sha256,
            parsed.filename,
            parsed.event_type,
            company_id,
            len(parsed.rubrics),
            len(parsed.remuneration_items),
            len(parsed.payments),
            " | ".join(parsed.warnings),
            now,
        ),
    )
    conn.commit()

    return {
        "filename": parsed.filename,
        "event_type": parsed.event_type,
        "status": "importado",
        "company_id": company_id,
        "rubricas": len(parsed.rubrics),
        "remuneracoes": len(parsed.remuneration_items),
        "pagamentos": len(parsed.payments),
        "warnings": parsed.warnings,
    }


def update_company_name(conn: sqlite3.Connection, company_id: int, razao_social: str) -> None:
    conn.execute(
        "UPDATE empresas SET razao_social = ?, updated_at = ? WHERE id = ?",
        (razao_social.strip() or None, _now(), company_id),
    )
    conn.commit()


def list_companies(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, tp_insc, nr_insc, cnpj_completo, COALESCE(razao_social, '') AS razao_social,
               class_trib, ind_des_folha, updated_at
        FROM empresas
        ORDER BY nr_insc
        """
    ).fetchall()
