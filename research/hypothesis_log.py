"""
Hypothesis Log (Hypothesis Graph)

Responsibility:
    Immutable, append-only log of every strategy variant that has ever been
    tested. Records:
        - strategy variant identifier
        - preregistered expected outcome (written BEFORE the test)
        - actual result
        - regime context in which it was tested

    This is the persistent memory that prevents re-testing of dead ideas.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import fcntl  # For file locking on Unix-like systems

logger = logging.getLogger(__name__)


class HypothesisLog:
    """Append-only, immutable hypothesis graph."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = Path(log_path)
        # Ensure directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure file exists
        if not self.log_path.exists():
            self.log_path.touch()
        logger.info("HypothesisLog initialized at %s", self.log_path)

    def preregister(self, variant_id: str, expected_outcome: Dict[str, Any], regime: str) -> str:
        """
        Write the expected outcome BEFORE any test runs.
        Returns a unique entry ID that must be used when recording the result.
        """
        # Generate unique entry ID
        entry_id = f"{variant_id}_{self._get_timestamp()}"
        
        # Create log entry
        entry = {
            "entry_id": entry_id,
            "variant_id": variant_id,
            "timestamp": self._get_timestamp_iso(),
            "regime": regime,
            "expected_outcome": expected_outcome,
            "actual_result": None,  # To be filled later
            "status": "preregistered",
        }
        
        # Atomically append to log file
        self._append_entry(entry)
        
        logger.debug("Preregistered variant %s with entry ID %s", variant_id, entry_id)
        return entry_id

    def record_result(self, entry_id: str, actual_result: Dict[str, Any]) -> None:
        """Append the actual result to the already-preregistered entry."""
        # Read the entire log to find the entry
        entries = self._read_all_entries()
        
        # Find the matching entry
        found = False
        for entry in entries:
            if entry.get("entry_id") == entry_id:
                if entry.get("status") != "preregistered":
                    logger.warning(
                        "Attempt to record result for non-preregistered entry %s", 
                        entry_id
                    )
                    return
                
                # Update the entry
                entry["actual_result"] = actual_result
                entry["status"] = "completed"
                entry["completed_timestamp"] = self._get_timestamp_iso()
                found = True
                break
        
        if not found:
            logger.error("Entry ID %s not found in hypothesis log", entry_id)
            raise ValueError(f"Entry ID {entry_id} not found")
        
        # Rewrite the entire log with updated entry
        self._rewrite_log(entries)
        
        logger.debug("Recorded result for entry ID %s", entry_id)

    def was_already_tested(self, variant_id: str, regime: str) -> bool:
        """Query whether this exact variant + regime combination has been tried."""
        entries = self._read_all_entries()
        
        for entry in entries:
            if (entry.get("variant_id") == variant_id and 
                entry.get("regime") == regime and
                entry.get("status") == "completed"):
                logger.debug(
                    "Found previous test: variant=%s, regime=%s, entry_id=%s",
                    variant_id, regime, entry.get("entry_id")
                )
                return True
        
        return False

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the hypothesis log."""
        entries = self._read_all_entries()
        
        total = len(entries)
        preregistered = sum(1 for e in entries if e.get("status") == "preregistered")
        completed = sum(1 for e in entries if e.get("status") == "completed")
        
        # Calculate success rate if we have actual results with measurable outcomes
        successful = 0
        measurable = 0
        for entry in entries:
            if entry.get("status") == "completed":
                actual = entry.get("actual_result", {})
                # Define success based on available metrics
                if "return" in actual:
                    measurable += 1
                    if actual["return"] > 0:
                        successful += 1
                elif "sharpe_ratio" in actual:
                    measurable += 1
                    if actual["sharpe_ratio"] > 0:
                        successful += 1
        
        success_rate = successful / measurable if measurable > 0 else 0.0
        
        return {
            "total_entries": total,
            "preregistered": preregistered,
            "completed": completed,
            "success_rate": success_rate,
            "measurable_outcomes": measurable,
            "successful_outcomes": successful,
        }

    # Private helper methods
    
    def _append_entry(self, entry: Dict[str, Any]) -> None:
        """Atomically append an entry to the log file."""
        # Use file locking to prevent race conditions
        max_retries = 5
        for attempt in range(max_retries):
            try:
                with open(self.log_path, 'a', encoding='utf-8') as f:
                    # Lock the file (Unix-specific)
                    if hasattr(fcntl, 'LOCK_EX'):
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    
                    # Write the entry as JSON line
                    f.write(json.dumps(entry) + '\n')
                    
                    # Unlock
                    if hasattr(fcntl, 'LOCK_UN'):
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    
                    return
                    
            except (IOError, OSError) as e:
                if attempt == max_retries - 1:
                    logger.error("Failed to append to hypothesis log after %d attempts: %s", 
                                max_retries, e)
                    raise
                logger.warning("Attempt %d failed to append to hypothesis log: %s", 
                              attempt + 1, e)
                # Brief pause before retry
                import time
                time.sleep(0.1 * (attempt + 1))

    def _read_all_entries(self) -> List[Dict[str, Any]]:
        """Read all entries from the log file."""
        entries = []
        if not self.log_path.exists():
            return entries
            
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                # Lock for reading
                if hasattr(fcntl, 'LOCK_SH'):
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            "Invalid JSON on line %d of hypothesis log: %s", 
                            line_num, e
                        )
                
                # Unlock
                if hasattr(fcntl, 'LOCK_UN'):
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    
        except (IOError, OSError) as e:
            logger.error("Failed to read hypothesis log: %s", e)
            return []
        
        return entries

    def _rewrite_log(self, entries: List[Dict[str, Any]]) -> None:
        """Rewrite the entire log file with given entries."""
        # Write to temporary file first, then atomic rename
        temp_path = self.log_path.with_suffix('.tmp')
        
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                # Lock the temp file
                if hasattr(fcntl, 'LOCK_EX'):
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                
                for entry in entries:
                    f.write(json.dumps(entry) + '\n')
                
                # Unlock
                if hasattr(fcntl, 'LOCK_UN'):
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            
            # Atomic rename (Unix-like systems)
            temp_path.replace(self.log_path)
            
        except (IOError, OSError) as e:
            logger.error("Failed to rewrite hypothesis log: %s", e)
            # Clean up temp file if it exists
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise

    def _get_timestamp(self) -> str:
        """Get compact timestamp for ID generation."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    def _get_timestamp_iso(self) -> str:
        """Get ISO format timestamp."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
