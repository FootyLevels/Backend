"""
Feedback Module
Generate human-readable, actionable feedback from analysis results.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
import random


@dataclass
class FeedbackItem:
    """Single piece of feedback."""
    category: str  # 'strength', 'improvement', 'tip'
    priority: int  # 1 = highest priority
    title: str
    detail: str
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    metric_target: Optional[float] = None


@dataclass
class StructuredFeedback:
    """Complete structured feedback for a skill attempt."""
    skill_name: str
    overall_rating: str  # 'excellent', 'good', 'needs_work', 'beginner'
    overall_score: float
    
    summary: str  # One-line summary
    
    strengths: List[FeedbackItem]
    improvements: List[FeedbackItem]
    tips: List[FeedbackItem]
    
    encouragement: str
    
    def to_dict(self) -> dict:
        return {
            'skill_name': self.skill_name,
            'overall_rating': self.overall_rating,
            'overall_score': round(self.overall_score, 1),
            'summary': self.summary,
            'strengths': [
                {
                    'title': s.title,
                    'detail': s.detail,
                    'priority': s.priority
                }
                for s in self.strengths
            ],
            'improvements': [
                {
                    'title': i.title,
                    'detail': i.detail,
                    'priority': i.priority,
                    'current_value': i.metric_value,
                    'target_value': i.metric_target
                }
                for i in self.improvements
            ],
            'tips': [
                {
                    'title': t.title,
                    'detail': t.detail
                }
                for t in self.tips
            ],
            'encouragement': self.encouragement
        }
    
    def to_text(self) -> str:
        """Convert to human-readable text format."""
        lines = [
            f"=== {self.skill_name} Analysis ===",
            f"Overall Score: {self.overall_score:.0f}/100 ({self.overall_rating})",
            "",
            self.summary,
            ""
        ]
        
        if self.strengths:
            lines.append("✅ What you did well:")
            for s in self.strengths:
                lines.append(f"  • {s.title}")
                if s.detail != s.title:
                    lines.append(f"    {s.detail}")
            lines.append("")
        
        if self.improvements:
            lines.append("🎯 Areas to improve:")
            for i in self.improvements:
                lines.append(f"  • {i.title}")
                lines.append(f"    {i.detail}")
                if i.metric_value is not None and i.metric_target is not None:
                    lines.append(f"    (Your value: {i.metric_value:.1f}, Target: {i.metric_target:.1f})")
            lines.append("")
        
        if self.tips:
            lines.append("💡 Tips:")
            for t in self.tips:
                lines.append(f"  • {t.detail}")
            lines.append("")
        
        lines.append(self.encouragement)
        
        return "\n".join(lines)


class FeedbackGenerator:
    """
    Generate structured feedback from analysis results.
    """
    
    # Rating thresholds
    RATING_EXCELLENT = 85
    RATING_GOOD = 70
    RATING_NEEDS_WORK = 50
    
    # Skill-specific tips
    SKILL_TIPS = {
        'cruyff_turn': [
            "Practice the move slowly first, focusing on the drag behind your standing leg",
            "Use your arms for balance during the turn",
            "Look over your shoulder in the direction you'll exit",
            "The key is deception - make the defender think you're going one way",
            "Practice against a cone before trying it in a game"
        ],
        'step_over': [
            "Keep your body relaxed and your movements loose",
            "The step over should be one fluid motion",
            "Lean your body into the fake to sell it",
            "Speed matters - a slow step over won't fool anyone",
            "Practice the rhythm: step over, push, accelerate"
        ],
        'elastico': [
            "This is an advanced move - master the basics first",
            "The snap from outside to inside must be lightning quick",
            "Keep the ball very close to your foot",
            "Use your ankle, not your whole leg",
            "Practice stationary before trying it in motion"
        ]
    }
    
    # Encouragement messages by rating
    ENCOURAGEMENT = {
        'excellent': [
            "Outstanding work! You're performing this skill at a high level. 🌟",
            "Fantastic execution! Keep pushing to make it even more instinctive.",
            "You've clearly put in the practice. This looks professional! 🏆"
        ],
        'good': [
            "Great job! You're on the right track. A few tweaks and you'll master this! 👍",
            "Solid performance! Focus on the areas mentioned and you'll level up fast.",
            "You've got the fundamentals down. Now let's polish the details."
        ],
        'needs_work': [
            "Good effort! Every pro started somewhere. Keep practicing! 💪",
            "You're building a good foundation. Focus on one improvement at a time.",
            "Rome wasn't built in a day - neither is a great Cruyff turn. Keep at it!"
        ],
        'beginner': [
            "Great start! This is a challenging skill - don't get discouraged. 🌱",
            "The hardest part is starting. You've done that - now keep going!",
            "Watch the reference video a few more times and try again. You've got this!"
        ]
    }
    
    def __init__(self):
        """Initialize feedback generator."""
        pass
    
    def generate(self, analysis_result) -> StructuredFeedback:
        """Generate structured feedback from analysis result."""
        # Determine overall rating
        score = analysis_result.overall_score
        if score >= self.RATING_EXCELLENT:
            rating = 'excellent'
        elif score >= self.RATING_GOOD:
            rating = 'good'
        elif score >= self.RATING_NEEDS_WORK:
            rating = 'needs_work'
        else:
            rating = 'beginner'
        
        # Generate summary
        summary = self._generate_summary(analysis_result, rating)
        
        # Process checkpoints into strengths and improvements
        strengths = []
        improvements = []
        
        for i, checkpoint in enumerate(analysis_result.checkpoints):
            if checkpoint.rating == 'good':
                strengths.append(FeedbackItem(
                    category='strength',
                    priority=i + 1,
                    title=checkpoint.name,
                    detail=checkpoint.feedback,
                    metric_name=checkpoint.checkpoint_id,
                    metric_value=checkpoint.value,
                    metric_target=checkpoint.ideal_value
                ))
            elif checkpoint.rating in ['acceptable', 'poor']:
                priority = 1 if checkpoint.rating == 'poor' else 2
                improvements.append(FeedbackItem(
                    category='improvement',
                    priority=priority,
                    title=checkpoint.name,
                    detail=checkpoint.feedback,
                    metric_name=checkpoint.checkpoint_id,
                    metric_value=checkpoint.value,
                    metric_target=checkpoint.ideal_value
                ))
        
        # Sort improvements by priority
        improvements.sort(key=lambda x: x.priority)
        
        # Get skill-specific tips
        skill_key = analysis_result.skill_name.lower().replace(' ', '_')
        available_tips = self.SKILL_TIPS.get(skill_key, [])
        
        # Select relevant tips
        tips = self._select_tips(improvements, available_tips)
        
        # Get encouragement message
        encouragement_options = self.ENCOURAGEMENT.get(rating, self.ENCOURAGEMENT['needs_work'])
        encouragement = random.choice(encouragement_options)
        
        return StructuredFeedback(
            skill_name=analysis_result.skill_name,
            overall_rating=rating,
            overall_score=score,
            summary=summary,
            strengths=strengths,
            improvements=improvements,
            tips=tips,
            encouragement=encouragement
        )
    
    def _generate_summary(self, result, rating: str) -> str:
        """Generate one-line summary."""
        skill = result.skill_name
        
        if rating == 'excellent':
            return f"Excellent {skill}! Your technique closely matches the professional reference."
        elif rating == 'good':
            return f"Good {skill}! You've got the basics down with room for refinement."
        elif rating == 'needs_work':
            return f"Your {skill} is developing. Focus on the key areas below."
        else:
            return f"Keep practicing your {skill}. Review the reference video and try again."
    
    def _select_tips(
        self, 
        improvements: List[FeedbackItem], 
        available_tips: List[str]
    ) -> List[FeedbackItem]:
        """Select most relevant tips."""
        if not available_tips:
            return []
        
        tips = []
        
        tips.append(FeedbackItem(
            category='tip',
            priority=1,
            title="Practice Tip",
            detail=available_tips[0]
        ))
        
        if len(improvements) > 0 and len(available_tips) > 1:
            tips.append(FeedbackItem(
                category='tip',
                priority=2,
                title="Pro Tip",
                detail=available_tips[1]
            ))
        
        return tips


def generate_feedback(analysis_result) -> StructuredFeedback:
    """Convenience function to generate feedback."""
    generator = FeedbackGenerator()
    return generator.generate(analysis_result)


if __name__ == "__main__":
    print("Feedback module loaded successfully.")
