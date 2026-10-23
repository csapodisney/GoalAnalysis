"""One declared-claim taxonomy for Grok output and immutable news ingestion."""

NEWS_KINDS=("INJURY","LINEUP","ROTATION","TRAVEL","COACH_STATEMENT","OTHER",
            "SALARY_PAYMENT","COACH_CHANGE","TEAM_CONFLICT","OTHER_HUMAN_FACTOR")
HUMAN_KINDS=frozenset({"SALARY_PAYMENT","COACH_CHANGE","TEAM_CONFLICT",
                       "COACH_STATEMENT","OTHER_HUMAN_FACTOR"})
