"""
Dashboard (optional)

Responsibility:
    Simple Streamlit style dashboard that visualizes:
        - current regimes
        - open paper positions
        - recent briefs
        - risk metrics & kill-switch status
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for the Streamlit dashboard."""
    st.set_page_config(
        page_title="RegimeDesk Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Custom CSS
    st.markdown("""
    <style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #ff6b6b;
    }
    .status-good { color: #00ff00; }
    .status-warning { color: #ffff00; }
    .status-error { color: #ff0000; }
    </style>
    """, unsafe_allow_html=True)
    
    st.title("📊 RegimeDesk Dashboard")
    st.caption("Paper Trading Only • Last updated: {}".format(
        _get_current_time()
    ))
    
    # Get root directory
    root = Path(__file__).resolve().parents[2]
    
    # Sidebar
    with st.sidebar:
        st.header("🔧 Controls")
        if st.button("🔄 Refresh Data"):
            st.rerun()
        
        st.subheader("📅 Schedule")
        st.text("Macro News: 22:30 UTC")
        st.text("Regime: 23:00 UTC")
        st.text("On-chain: 23:30 UTC")
        st.text("Sentiment: 00:30 UTC")
        st.text("Technical: 01:00 UTC")
        st.text("Chief of Staff: 05:30 UTC")
    
    # Main content
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📈 Overview", 
        "📊 Regimes", 
        "💼 Positions", 
        "📋 Briefs", 
        "⚠️ Risk"
    ])
    
    with tab1:
        _render_overview_tab(root)
    
    with tab2:
        _render_regimes_tab(root)
    
    with tab3:
        _render_positions_tab(root)
    
    with tab4:
        _render_briefs_tab(root)
    
    with tab5:
        _render_risk_tab(root)


def _render_overview_tab(root: Path) -> None:
    """Render the overview tab."""
    st.header("📊 Market Overview")
    
    # Get snapshot data
    snapshot_file = root / "state" / "desk_snapshot.json"
    if snapshot_file.exists():
        try:
            snapshot = json.loads(snapshot_file.read_text())
            _display_snapshot_overview(snapshot)
        except Exception as e:
            st.error(f"Failed to load snapshot: {e}")
            logger.error("Failed to load snapshot: %s", e)
    else:
        st.info("No snapshot data available. Run `python main.py research` to generate data.")


def _render_regimes_tab(root: Path) -> None:
    """Render the regimes tab."""
    st.header("📊 Current Market Regimes")
    
    # Try to get from snapshot first
    snapshot_file = root / "state" / "desk_snapshot.json"
    if snapshot_file.exists():
        try:
            snapshot = json.loads(snapshot_file.read_text())
            instruments = snapshot.get("instruments", [])
            if instruments:
                _display_regimes_table(instruments)
                return
        except Exception as e:
            logger.warning("Failed to get regimes from snapshot: %s", e)
    
    # Fallback to direct regime file
    regimes_file = root / "state" / "current_regimes.json"
    if regimes_file.exists():
        try:
            regimes = json.loads(regimes_file.read_text())
            _display_regimes_dict(regimes)
        except Exception as e:
            st.error(f"Failed to load regimes: {e}")
    else:
        st.info("No regime data available")


def _render_positions_tab(root: Path) -> None:
    """Render the positions tab."""
    st.header("💼 Open Paper Positions")
    
    # This would normally connect to brokers, but for demo we'll show placeholder
    st.info("Position tracking would connect to paper brokers (Alpaca/OANDA/CCXT)")
    
    # Show sample data structure
    sample_positions = [
        {
            "Symbol": "EURUSD",
            "Side": "Long",
            "Quantity": 10000,
            "Entry Price": 1.0850,
            "Current Price": 1.0875,
            "Market Value": "$10,875",
            "Unrealized P&L": "+$250 (+0.23%)",
        },
        {
            "Symbol": "BTCUSDT",
            "Side": "Short", 
            "Quantity": 0.15,
            "Entry Price": 42000,
            "Current Price": 41500,
            "Market Value": "$6,225",
            "Unrealized P&L": "+$75 (+1.22%)",
        }
    ]
    
    if sample_positions:
        st.dataframe(sample_positions, use_container_width=True)
    else:
        st.info("No open positions")


def _render_briefs_tab(root: Path) -> None:
    """Render the briefs tab."""
    st.header("📋 Research Briefs")
    
    briefs_dir = root / "briefs"
    if not briefs_dir.exists():
        st.info("No briefs directory found")
        return
    
    brief_files = list(briefs_dir.glob("*.md"))
    if not brief_files:
        st.info("No brief files found")
        return
    
    # Group by agent type
    briefs_by_agent: Dict[str, List[Path]] = {}
    for brief_file in brief_files:
        # Extract agent name from filename (e.g., macro_20260823.md -> macro)
        agent_name = brief_file.stem.split('_')[0]
        if agent_name not in briefs_by_agent:
            briefs_by_agent[agent_name] = []
        briefs_by_agent[agent_name].append(brief_file)
    
    # Display tabs for each agent type
    if briefs_by_agent:
        agent_tabs = st.tabs(list(briefs_by_agent.keys()))
        for agent_tab, (agent_name, files) in zip(agent_tabs, briefs_by_agent.items()):
            with agent_tab:
                st.subheader(f"{agent_name.upper()} Briefs")
                # Sort by date (newest first)
                sorted_files = sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)
                for brief_file in sorted_files[:5]:  # Show latest 5
                    with st.expander(f"📄 {brief_file.name}"):
                        try:
                            content = brief_file.read_text(encoding='utf-8')
                            st.text(content)
                        except Exception as e:
                            st.error(f"Failed to read {brief_file.name}: {e}")
    else:
        st.info("No brief files found")


def _render_risk_tab(root: Path) -> None:
    """Render the risk metrics tab."""
    st.header("⚠️ Risk Metrics & Kill Switch")
    
    # Load risk limits
    risk_file = root / "config" / "risk_limits.yaml"
    if risk_file.exists():
        try:
            import yaml
            risk_limits = yaml.safe_load(risk_file.read_text())
            _display_risk_limits(risk_limits)
        except Exception as e:
            st.error(f"Failed to load risk limits: {e}")
    else:
        st.warning("Risk limits file not found")
    
    st.divider()
    
    # Kill switch status
    st.subheader("🚨 Kill Switch Status")
    # This would come from the risk manager in practice
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Kill Switch", "ACTIVE", delta="⚠️", delta_color="inverse")
    with col2:
        st.metric("Consecutive Losses", "0 / 5", delta="Good")
    
    st.info("Kill switch would be controlled by RiskManager in live operation")


def _display_snapshot_overview(snapshot: Dict[str, Any]) -> None:
    """Display overview from snapshot data."""
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="Last Updated",
            value=snapshot.get("generated_at", "Unknown")[:19].replace("T", " ")
        )
    
    with col2:
        st.metric(
            label="Data Source",
            value=snapshot.get("source", "Unknown").title()
        )
    
    with col3:
        st.metric(
            label="Mode",
            value=snapshot.get("mode", "Unknown").title()
        )
    
    with col4:
        instruments_count = len(snapshot.get("instruments", []))
        st.metric(
            label="Instruments Tracked",
            value=instruments_count
        )
    
    # Show calendar events
    calendar = snapshot.get("calendar", [])
    if calendar:
        st.subheader("📅 Upcoming Economic Events")
        event_data = []
        for event in calendar[:10]:  # Show next 10 events
            event_data.append({
                "Time": event.get("time", "Unknown")[:16].replace("T", " "),
                "Currency": event.get("currency", ""),
                "Event": event.get("title", ""),
                "Impact": event.get("impact", ""),
                "Historical Move": f"{event.get('historically_moves_pct', 0):.1f}%"
            })
        if event_data:
            st.dataframe(event_data, use_container_width=True)
    
    # Show research summary
    research = snapshot.get("research", {})
    if research:
        st.subheader("🔬 Research Summary")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Macro Events", research.get("macro", {}).get("event_count", 0))
        with col2:
            st.metric("Flagged Events", research.get("macro", {}).get("flagged_count", 0))
        with col3:
            st.metric("On-chain Findings", len(research.get("onchain", {}).get("findings", [])))
        with col4:
            st.metric("Sentiment Findings", len(research.get("sentiment", {}).get("findings", [])))


def _display_regimes_table(instruments: List[Dict[str, Any]]) -> None:
    """Display regimes in a table format."""
    if not instruments:
        st.info("No regime data available")
        return
    
    regime_data = []
    for instrument in instruments:
        regime_data.append({
            "Symbol": instrument.get("ticker", "Unknown"),
            "Asset Class": instrument.get("asset_class", "Unknown").title(),
            "Regime": instrument.get("regime", "Unknown"),
            "Confidence": f"{instrument.get('confidence', 0):.0%}",
            "Trend": f"{instrument.get('technical', {}).get('direction', 'none')}",
            "Structure": f"{instrument.get('technical', {}).get('structure', 'range')}",
            "Last Close": f"{instrument.get('last_close', 0):.4f}",
            "Change %": f"{instrument.get('change_pct', 0):+.2f}%",
        })
    
    if regime_data:
        st.dataframe(regime_data, use_container_width=True)
    else:
        st.info("No regime data to display")


def _display_regimes_dict(regimes: Dict[str, Any]) -> None:
    """Display regimes from dictionary format."""
    if not regimes:
        st.info("No regime data available")
        return
    
    regime_data = []
    for symbol, data in regimes.items():
        regime_data.append({
            "Symbol": symbol,
            "Regime": data.get("label", "Unknown"),
            "Confidence": f"{data.get('confidence', 0):.0%}",
        })
    
    if regime_data:
        st.dataframe(regime_data, use_container_width=True)
    else:
        st.info("No regime data to display")


def _display_risk_limits(risk_limits: Dict[str, Any]) -> None:
    """Display risk limits in an organized way."""
    st.subheader("📋 Current Risk Limits")
    
    # Account info
    account = risk_limits.get("account", {})
    if account:
        st.write("**Account**")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Starting Equity", f"${account.get('starting_equity', 0):,.2f}")
        with col2:
            st.metric("Base Currency", account.get("base_currency", "USD"))
    
    # Position sizing
    pos_sizing = risk_limits.get("position_sizing", {})
    if pos_sizing:
        st.write("**Position Sizing**")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Risk per Trade", f"{pos_sizing.get('risk_per_trade_pct', 0)}%")
        with col2:
            st.metric("Max Position Size", f"{pos_sizing.get('max_position_size_pct', 0)}%")
        with col3:
            st.metric("Kelly Fraction", f"{pos_sizing.get('kelly_fraction', 0)}")
    
    # Drawdown limits
    drawdown = risk_limits.get("drawdown", {})
    if drawdown:
        st.write("**Drawdown Limits**")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Daily Max", f"{drawdown.get('max_daily_pct', 0)}%")
        with col2:
            st.metric("Weekly Max", f"{drawdown.get('max_weekly_pct', 0)}%")
        with col3:
            st.metric("Monthly Max", f"{drawdown.get('max_monthly_pct', 0)}%")
        with col4:
            st.metric("Recovery Mode", "On" if drawdown.get('recovery_mode', True) else "Off")
    
    # Exposure limits
    exposure = risk_limits.get("exposure", {})
    if exposure:
        st.write("**Exposure Limits**")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Max Positions", exposure.get('max_concurrent_positions', 0))
        with col2:
            st.metric("Max Forex", exposure.get('max_forex_positions', 0))
        with col3:
            st.metric("Max Crypto", exposure.get('max_crypto_positions', 0))
    
    # Forex specifics
    forex = risk_limits.get("forex", {})
    if forex:
        st.write("**Forex Rules**")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Max Leverage", f"{forex.get('max_leverage', 0)}x")
        with col2:
            st.metric("Max Spread", f"{forex.get('max_spread_pips', 0)} pips")
        with col3:
            st.metric("News Avoidance", f"±{forex.get('avoid_news_minutes_before', 0)}/{forex.get('avoid_news_minutes_after', 0)} min")
        with col4:
            st.metric("Weekend Flat", "On" if forex.get('weekend_flat', True) else "Off")
    
    # Crypto specifics
    crypto = risk_limits.get("crypto", {})
    if crypto:
        st.write("**Crypto Rules**")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Max Leverage", f"{crypto.get('max_leverage_perps', 0)}x")
        with col2:
            st.metric("Max Funding Rate", f"{crypto.get('max_funding_rate_abs', 0):.4f}")
        with col3:
            st.metric("Funding Window", f"{crypto.get('avoid_funding_window_minutes', 0)} min")


def _get_current_time() -> str:
    """Get current time as formatted string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


if __name__ == "__main__":
    main()
