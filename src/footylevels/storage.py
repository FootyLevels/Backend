"""
Storage Module
Save and load analysis results.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List
import pickle


class AnalysisStorage:
    """
    Save and retrieve analysis results.

    Usage:
        storage = AnalysisStorage()
        storage.save_analysis(result, feedback, "my_attempt")
        results = storage.list_analyses()
    """

    def __init__(self, storage_dir: str = "data/results"):
        """
        Initialize storage.

        Args:
            storage_dir: Directory to store analysis results
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.json_dir = self.storage_dir / "json"
        self.json_dir.mkdir(exist_ok=True)

        self.pickle_dir = self.storage_dir / "pickle"
        self.pickle_dir.mkdir(exist_ok=True)

    def save_analysis(
        self,
        analysis_result,
        feedback=None,
        name: Optional[str] = None,
        user_video_path: Optional[str] = None,
        ref_video_path: Optional[str] = None
    ) -> str:
        """
        Save analysis result.

        Args:
            analysis_result: AnalysisResult object
            feedback: StructuredFeedback object (optional)
            name: Custom name for this analysis
            user_video_path: Path to user video (for reference)
            ref_video_path: Path to reference video

        Returns:
            ID of saved analysis
        """
        # Generate unique ID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if name:
            analysis_id = f"{name}_{timestamp}"
        else:
            analysis_id = f"{analysis_result.skill_name.lower().replace(' ', '_')}_{timestamp}"

        # Prepare data
        data = {
            "id": analysis_id,
            "timestamp": datetime.now().isoformat(),
            "skill": analysis_result.skill_name,
            "analysis": analysis_result.to_dict(),
            "user_video": str(user_video_path) if user_video_path else None,
            "reference_video": str(ref_video_path) if ref_video_path else None
        }

        if feedback:
            data["feedback"] = feedback.to_dict()

        # Save as JSON
        json_path = self.json_dir / f"{analysis_id}.json"
        with open(json_path, 'w') as f:
            json.dump(data, f, indent=2)

        # Save full objects as pickle (for later reprocessing)
        pickle_path = self.pickle_dir / f"{analysis_id}.pkl"
        with open(pickle_path, 'wb') as f:
            pickle.dump({
                "analysis_result": analysis_result,
                "feedback": feedback,
                "metadata": data
            }, f)

        print(f"✅ Analysis saved: {analysis_id}")
        print(f"   JSON: {json_path}")

        return analysis_id

    def load_analysis(self, analysis_id: str) -> dict:
        """
        Load analysis by ID.

        Args:
            analysis_id: ID of the analysis

        Returns:
            Dictionary with analysis data
        """
        json_path = self.json_dir / f"{analysis_id}.json"

        if not json_path.exists():
            raise FileNotFoundError(f"Analysis not found: {analysis_id}")

        with open(json_path, 'r') as f:
            return json.load(f)

    def load_analysis_objects(self, analysis_id: str) -> dict:
        """
        Load full analysis objects (from pickle).

        Returns:
            Dict with analysis_result, feedback, and metadata
        """
        pickle_path = self.pickle_dir / f"{analysis_id}.pkl"

        if not pickle_path.exists():
            raise FileNotFoundError(f"Analysis objects not found: {analysis_id}")

        with open(pickle_path, 'rb') as f:
            return pickle.load(f)

    def list_analyses(self, skill: Optional[str] = None) -> List[dict]:
        """
        List all saved analyses.

        Args:
            skill: Filter by skill name (optional)

        Returns:
            List of analysis summaries
        """
        analyses = []

        for json_file in sorted(self.json_dir.glob("*.json"), reverse=True):
            with open(json_file, 'r') as f:
                data = json.load(f)

                # Filter by skill if specified
                if skill and data['skill'].lower() != skill.lower():
                    continue

                analyses.append({
                    "id": data['id'],
                    "timestamp": data['timestamp'],
                    "skill": data['skill'],
                    "overall_score": data['analysis']['overall_score'],
                    "overall_rating": data.get('feedback', {}).get('overall_rating', 'unknown')
                })

        return analyses

    def delete_analysis(self, analysis_id: str):
        """Delete an analysis."""
        json_path = self.json_dir / f"{analysis_id}.json"
        pickle_path = self.pickle_dir / f"{analysis_id}.pkl"

        if json_path.exists():
            json_path.unlink()

        if pickle_path.exists():
            pickle_path.unlink()

        print(f"Deleted analysis: {analysis_id}")

    def export_to_csv(self, output_path: str, skill: Optional[str] = None):
        """
        Export analyses to CSV.

        Args:
            output_path: Where to save CSV
            skill: Filter by skill (optional)
        """
        import csv

        analyses = self.list_analyses(skill)

        if not analyses:
            print("No analyses to export")
            return

        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'id', 'timestamp', 'skill', 'overall_score', 'overall_rating'
            ])
            writer.writeheader()
            writer.writerows(analyses)

        print(f"Exported {len(analyses)} analyses to {output_path}")

    def get_progress_summary(self, skill: str) -> dict:
        """
        Get progress summary for a skill over time.

        Args:
            skill: Skill name

        Returns:
            Dict with progress statistics
        """
        analyses = self.list_analyses(skill)

        if not analyses:
            return {
                "skill": skill,
                "total_attempts": 0,
                "average_score": 0,
                "best_score": 0,
                "latest_score": 0
            }

        scores = [a['overall_score'] for a in analyses]

        return {
            "skill": skill,
            "total_attempts": len(analyses),
            "average_score": sum(scores) / len(scores),
            "best_score": max(scores),
            "latest_score": scores[0],  # Most recent
            "improvement": scores[0] - scores[-1] if len(scores) > 1 else 0
        }


def save_analysis_result(
    analysis_result,
    feedback=None,
    name: Optional[str] = None,
    storage_dir: str = "data/results"
) -> str:
    """
    Convenience function to save an analysis.

    Args:
        analysis_result: AnalysisResult object
        feedback: StructuredFeedback object
        name: Custom name
        storage_dir: Storage directory

    Returns:
        Analysis ID
    """
    storage = AnalysisStorage(storage_dir)
    return storage.save_analysis(analysis_result, feedback, name)


# Quick test
if __name__ == "__main__":
    print("Storage module loaded successfully.")
    print("\nTo save analysis results:")
    print("  storage = AnalysisStorage()")
    print("  storage.save_analysis(result, feedback, 'my_attempt')")
