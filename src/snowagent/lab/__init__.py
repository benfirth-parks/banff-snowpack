"""Snowpack Agent Lab (ADR-055): a local research benchmark in which snowpack-prediction agents predict the observed
pit at Bow Summit, Goat's Eye and Simpson from the data available at a cut-off, scored against withheld pits.

Research and decision support only, never an avalanche forecast. SNOWPACK (the site runs) is the incumbent agent;
other agents' layers are benchmark entries and never site output. Nothing here is imported by the daily run; the UI
and storage packages (streamlit, plotly, pyarrow, scikit-learn: the optional ``lab`` extra) are imported lazily.
"""

LAB_DISCLAIMER = (
    "Research and decision-support product only. Outputs are never a substitute for field assessment, professional "
    "avalanche forecasting or local observations, and are not an avalanche forecast or danger rating."
)
