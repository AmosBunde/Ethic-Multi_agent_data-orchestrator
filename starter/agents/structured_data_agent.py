import pandas as pd
import numpy as np

from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from langchain_openai import AzureChatOpenAI
from langgraph.prebuilt import create_react_agent

from sqlalchemy import create_engine, inspect
from urllib.parse import quote_plus

from fairlearn.metrics import (
    MetricFrame,
    selection_rate,
    demographic_parity_difference,
)


class StructuredDataAgent:

    # ============================================
    # INIT
    # ============================================
    def __init__(
        self,
        azure_endpoint,
        api_key,
        deployment,
        db_config,
        api_version="2024-12-01-preview",
        temperature=0.7,
    ):
        encoded_password = quote_plus(db_config["password"])
        # psycopg 3 driver requires the "postgresql+psycopg" dialect prefix.
        self.db_uri = (
            f"postgresql+psycopg://{db_config['user']}:{encoded_password}"
            f"@{db_config['host']}:{db_config.get('port', 5432)}/{db_config['dbname']}"
        )

        self.model = AzureChatOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            azure_deployment=deployment,
            api_version=api_version,
            temperature=temperature,
        )

        self.db = SQLDatabase.from_uri(self.db_uri)
        toolkit = SQLDatabaseToolkit(db=self.db, llm=self.model)
        tools = toolkit.get_tools()

        self.agent_executor = create_react_agent(self.model, tools)

    # ============================================
    # PUBLIC METHOD
    # ============================================
    def ask(self, question, verbose=False, run_bias_audit=True):

        response_text = ""

        for chunk in self.agent_executor.stream(
            {"messages": [("user", question)]},
            stream_mode="values",
        ):
            msg = chunk["messages"][-1]
            response_text = msg.content or ""
            if verbose:
                print(msg.pretty_repr())

        audit = {"ran": bool(run_bias_audit), "findings": [], "flagged": False}

        if run_bias_audit:
            print("\n" + "=" * 80)
            print("Using Strucutred Data stored in PostgresQL database to answer question")
            print("Performing a Fairness audit before providing answer")
            print("=" * 80)
            findings = self.__auto_bias_check(self.db_uri)
            flagged = [f for f in findings if f["flagged"]]
            audit["findings"] = findings
            audit["flagged"] = bool(flagged)

            # Actionable check: the answer carries a fairness advisory when the
            # underlying data shows disparities across demographic groups.
            if flagged:
                response_text = self.__bias_advisory(flagged) + "\n\n" + response_text

        return {"question": question, "response": response_text, "audit": audit}

    # ============================================
    # ADVISORY TEXT
    # ============================================
    def __bias_advisory(self, flagged):
        groups = sorted({f["demographic_column"] for f in flagged})
        examples = "; ".join(
            f"{f['table']}.{f['column']} by {f['demographic_column']} ({f['metric']}={f['value']:.2f})"
            for f in flagged[:3]
        )
        more = f" and {len(flagged) - 3} more" if len(flagged) > 3 else ""
        return (
            "FAIRNESS ADVISORY: the data behind this answer shows disparities across "
            f"{', '.join(groups)}. Examples: {examples}{more}. "
            "Group-level comparisons should not be used to make decisions about individuals."
        )

    # ============================================
    # TABLE DISCOVERY
    # ============================================
    def __discover_tables(self, db_uri):

        engine = create_engine(db_uri)
        inspector = inspect(engine)

        tables = []
        for schema in inspector.get_schema_names():
            if schema in ["information_schema", "pg_catalog"]:
                continue

            for table in inspector.get_table_names(schema=schema):
                full_name = f"{schema}.{table}" if schema != "public" else table
                tables.append(full_name)

        return tables, engine

    # ============================================
    # DEMOGRAPHIC DETECTION
    # ============================================
    def __find_demographic_columns(self, columns):

        keywords = [
            "gender", "sex",
            "race", "ethnicity", "ethnic",
            "age",
            "religion",
            "disability",
            "marital",
            "nationality", "citizenship", "immigration",
            "language",
            "education",
            "veteran",
            "orientation",
        ]

        # Match whole tokens of snake_case identifiers so "age" does not hit "average".
        return [
            col for col in columns
            if any(tok in keywords for tok in str(col).lower().split("_"))
        ]

    # ============================================
    # FAIRNESS ENGINE
    # ============================================
    def __run_fairness_analysis(self, df, table_name, demographic_column):

        findings = []

        def record(column, kind, metric, value, threshold, flagged):
            findings.append({
                "table": table_name,
                "demographic_column": demographic_column,
                "column": column,
                "type": kind,
                "metric": metric,
                "value": float(value),
                "threshold": threshold,
                "flagged": bool(flagged),
            })

        print("\n" + "-" * 70)
        print(f"TABLE: {table_name}")
        print(f"DEMOGRAPHIC COLUMN: {demographic_column}")
        print("-" * 70)

        if df[demographic_column].nunique() > 10:
            print("Skipping: demographic column has more than 10 distinct groups "
                  "(continuous attribute, bucket it first)")
            return findings

        for col in df.columns:

            if col == demographic_column or col == "id":
                continue

            temp = df[[demographic_column, col]].dropna()
            if temp.empty:
                continue

            print(f"\nAnalyzing column: {col}")

            unique_count = temp[col].nunique()
            values = sorted(temp[col].unique(), key=str)
            bool_like = set(map(str, values)) <= {"0", "1", "True", "False", "true", "false"}
            is_binary = unique_count == 2 and (bool_like or not pd.api.types.is_numeric_dtype(temp[col]))

            # ============================================
            # BINARY → Fairlearn
            # ============================================
            if is_binary:

                print("Type: Binary (classification fairness)")

                # Fairlearn's selection_rate expects 0/1 labels. Map the two observed
                # values explicitly so booleans and yes/no style strings produce a
                # meaningful rate instead of silently reporting 0.
                if bool_like:
                    positive = [v for v in values if str(v) in ("1", "True", "true")][0]
                else:
                    positive = values[-1]
                print(f"Positive label: {positive!r}")
                y = (temp[col] == positive).astype(int)
                sensitive = temp[demographic_column]

                metric_frame = MetricFrame(
                    metrics={"selection_rate": selection_rate},
                    y_true=y,
                    y_pred=y,
                    sensitive_features=sensitive,
                )

                print("\nSelection Rate by Group:")
                print(metric_frame.by_group)

                dp_diff = demographic_parity_difference(
                    y_true=y,
                    y_pred=y,
                    sensitive_features=sensitive,
                )

                print(f"Demographic Parity Difference: {dp_diff:.4f}")

                flagged = abs(dp_diff) > 0.10
                record(col, "binary", "demographic_parity_difference", dp_diff, 0.10, flagged)
                if flagged:
                    print("Potential bias detected")
                else:
                    print("Within fairness threshold")

            # ============================================
            # NUMERIC → Mean Comparison
            # ============================================
            elif pd.api.types.is_numeric_dtype(temp[col]):

                print("Type: Numeric (distribution fairness)")

                means = temp.groupby(demographic_column)[col].mean()
                print("\nMean by group:")
                print(means)

                min_val = means.min()
                max_val = means.max()
                ratio = min_val / max_val if max_val > 0 else 0

                print(f"Mean ratio (min/max): {ratio:.3f}")

                flagged = ratio < 0.80
                record(col, "numeric", "mean_ratio_min_max", ratio, 0.80, flagged)
                if flagged:
                    print("Large disparity detected")
                else:
                    print("No major disparity")

            # ============================================
            # SMALL CATEGORICAL → Distribution Compare
            # ============================================
            elif 2 < unique_count <= 10:

                print("Type: Categorical (distribution comparison)")

                dist = pd.crosstab(
                    temp[demographic_column],
                    temp[col],
                    normalize="index"
                ) * 100

                print("\nPercentage distribution by group:")
                print(dist.round(2))
                # Largest spread of any category share across groups, in percentage points.
                spread = float((dist.max() - dist.min()).max())
                record(col, "categorical", "max_share_spread_pct", spread, 20.0, spread > 20.0)

            else:
                print("Skipping (too many categories)")

        return findings

    # ============================================
    # AUTO BIAS CHECK
    # ============================================
    def __auto_bias_check(self, db_uri):

        tables, engine = self.__discover_tables(db_uri)
        findings = []

        for table_name in tables:

            df = pd.read_sql(f"SELECT * FROM {table_name}", engine)

            demographic_cols = self.__find_demographic_columns(df.columns)

            if not demographic_cols:
                continue

            for demo_col in demographic_cols:
                findings.extend(self.__run_fairness_analysis(df, table_name, demo_col))

        return findings
