# Indenizatax

MVP local para importar XMLs do eSocial, cadastrar empresas automaticamente, cruzar rubricas com remunerações e gerar relatórios para triagem de verbas potencialmente indenizatórias vinculadas a PER/DCOMP.

## Rodar

```powershell
cd D:\SISTEMAS\Indenizatax
py -m pip install -r requirements.txt
py -m streamlit run app.py
```

Depois abra o endereço exibido pelo Streamlit. Normalmente será `http://localhost:8501`.

## XMLs corretos para análise

Para apurar verbas, importe principalmente:

- `S-1010` - Tabela de Rubricas. Traz `codRubr`, `dscRubr`, `natRubr`, `codIncCP`, `codIncIRRF` e `codIncFGTS`.
- `S-1200`, `S-1202` ou `S-1207` - Remunerações/benefícios com itens de remuneração.
- `S-2299` e `S-2399` - Desligamento/término, quando houver verbas rescisórias.
- `S-1210` - Pagamentos, útil para vínculo com demonstrativos.

Arquivos de fechamento `S-1299` confirmam a competência, mas não trazem rubricas nem valores por verba.

## Saídas

- Relatório analítico por trabalhador, rubrica, competência e valor.
- Consolidado por mês/rubrica.
- Consolidado anual.
- Exportação CSV.
- Dossiê Excel completo.

## Observação

O sistema faz triagem e dossiê de conferência. A validação fiscal, jurídica, DCTFWeb, DARF pago e retificações continuam sendo etapas obrigatórias antes de qualquer recuperação de crédito.
