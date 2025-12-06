# FootyLevels Scoring Approaches Explained

## Overview

FootyLevels has **two scoring approaches** for analyzing soccer skills. Both extract poses, align sequences, and measure key checkpoints, but they **score differently**.

---

## 1. **Absolute Scoring** (Reference-Based)

### What It Does

Compares the user's performance against **pre-defined ideal values** stored in `config/skills.yaml`.

### How It Works

1. **Extract Metrics**: Measures user's actual values (e.g., hip rotation = 86.7°)
2. **Compare to Ideal**: Compares against ideal value from config (e.g., ideal = 166°)
3. **Score Based on Deviation**: 
   - Close to ideal = high score
   - Far from ideal = low score

### Example: Hip Rotation Checkpoint

```
User's hip rotation: 86.7°
Ideal hip rotation: 166.0° (from config/skills.yaml)
Difference: 79.3°

Scoring:
- If difference ≤ 10% of ideal → "good" (score: 100)
- If difference ≤ 30% of ideal → "acceptable" (score: 70)
- Otherwise → "poor" (score: 30-50)

Result: 86.7° is 48% off from 166° → "poor" rating, score: ~52
```

### Code Location

**File**: `src/footylevels/analyzer.py`
**Method**: `analyze_skill()` → `_evaluate_single_checkpoint()`

```python
# Lines 247-316 in analyzer.py
ideal_value = checkpoint.get('ideal_value', 0)  # From config
measured_value = self._calculate_checkpoint_metric(...)  # User's value

# Score based on how close measured_value is to ideal_value
diff = abs(measured_value - ideal_value)
if diff <= threshold_good:
    score = 100
elif diff <= threshold_acceptable:
    score = 70
else:
    score = 30
```

### Pros ✅

- **Educational**: Teaches users what "perfect" biomechanical form looks like
- **Objective**: Based on sports science/biomechanics research
- **Scalable**: Can exceed reference video performance
- **Consistent**: Same ideal values for all users

### Cons ❌

- **Requires Calibration**: Each reference video needs its ideal values calibrated
- **Confusing**: Reference video itself might score 70-75 (not 100)
- **Less Intuitive**: "Why doesn't the pro video get 100?"

### When to Use

- **Production/Advanced**: When you have calibrated ideal values
- **Educational Apps**: Teaching proper technique
- **Biomechanical Analysis**: Research or coaching applications

---

## 2. **Comparative Scoring** (Relative to Reference)

### What It Does

Compares the user's performance **directly to the reference video** performance.

### How It Works

1. **Extract Metrics for Both**: 
   - Measure reference video's values (e.g., ref hip rotation = 86.7°)
   - Measure user video's values (e.g., user hip rotation = 93.6°)
2. **Calculate Deviation**: How much user differs from reference (percentage)
3. **Score Based on Match**:
   - Within 10% of reference → "excellent" (90-100)
   - Within 20% of reference → "good" (75-90)
   - Within 35% of reference → "acceptable" (60-75)
   - Beyond 35% → "poor" (<60)

### Example: Hip Rotation Checkpoint

```
Reference video hip rotation: 86.7°
User video hip rotation: 93.6°
Difference: 6.9° (8% deviation)

Scoring:
- Deviation = 8% (within 10% threshold)
- Score = 100 - (0.08 * 100) = 92
- Rating: "excellent"

Result: User matches reference closely → 92/100
```

### Code Location

**File**: `src/footylevels/comparative_analyzer.py`
**Method**: `analyze_skill_comparative()` → `ComparativeScoring.calculate_deviation_score()`

```python
# Lines 42-79 in comparative_analyzer.py
ref_value = analyzer._calculate_checkpoint_metric(..., ref_metrics, ...)
user_value = analyzer._calculate_checkpoint_metric(..., user_metrics, ...)

# Calculate percentage deviation
deviation = abs(user_value - ref_value) / abs(ref_value)

# Score based on deviation thresholds
if deviation <= 0.10:  # Within 10%
    score = 100 - (deviation * 100)  # 90-100 range
elif deviation <= 0.20:  # Within 20%
    score = 85 - ((deviation - 0.10) * 150)  # 70-90 range
# ... etc
```

### Pros ✅

- **Intuitive**: Reference video = benchmark (scores ~95-100)
- **No Calibration**: Works immediately with any reference video
- **Easy to Understand**: "Match the reference" is clear
- **Quick Setup**: Just need good reference videos

### Cons ❌

- **Limited by Reference**: Can't easily exceed reference performance
- **Reference Quality Matters**: Poor reference = poor scoring
- **Less Educational**: Doesn't teach "ideal" biomechanics

### When to Use

- **MVP/Quick Launch**: Fastest way to get started
- **User-Facing Apps**: More intuitive for end users
- **Progress Tracking**: "Match the pro" is motivating

---

## Side-by-Side Comparison

### Scenario: User performs Cruyff Turn

| Metric | User Value | Reference Value | Ideal (Config) |
|--------|-----------|-----------------|----------------|
| Hip Rotation | 93.6° | 86.7° | 166.0° |

### Absolute Scoring Result

```
Comparison: User (93.6°) vs Ideal (166.0°)
Difference: 72.4° (44% off)
Score: ~52/100
Rating: "poor"
Feedback: "Rotate your hips more fully - aim for 180°"
```

**Logic**: User is far from the biomechanically ideal value.

### Comparative Scoring Result

```
Comparison: User (93.6°) vs Reference (86.7°)
Difference: 6.9° (8% deviation)
Score: ~92/100
Rating: "excellent"
Feedback: "Excellent! Your hip rotation matches the reference closely."
```

**Logic**: User matches the reference video well.

---

## Real Example from Your Data

Looking at `cruyff_turn_20251204_161436.json`:

### Absolute Scoring (Current Result)

```json
{
  "hip_rotation": {
    "value": 93.642,
    "ideal": 86.67393025330911,  // From reference video calibration
    "score": 92.0,
    "rating": "excellent"
  }
}
```

**What happened**: The system calibrated the reference video and found its hip rotation was 86.7°. Then it compared user's 93.6° to that calibrated value. User was close → good score.

### Comparative Scoring (If Used)

```json
{
  "hip_rotation": {
    "user_value": 93.642,
    "ref_value": 86.673,  // Direct from reference video
    "deviation_percent": 8.0,
    "score": 92.0,
    "rating": "excellent"
  }
}
```

**What happened**: Directly compared user (93.6°) to reference (86.7°). 8% deviation → excellent score.

**Note**: In this case, both approaches give similar scores, but the **logic is different**:
- **Absolute**: "User is close to the calibrated reference value"
- **Comparative**: "User matches the reference video closely"

---

## Key Differences Summary

| Aspect | Absolute Scoring | Comparative Scoring |
|--------|------------------|---------------------|
| **Benchmark** | Pre-defined ideal values | Reference video performance |
| **Calibration** | Required (run calibration script) | Not needed |
| **Reference Score** | ~70-75 (not perfect) | ~95-100 (near perfect) |
| **User Understanding** | "Match biomechanical ideal" | "Match the reference video" |
| **Setup Time** | Longer (calibrate each skill) | Faster (just need videos) |
| **Best For** | Production, education | MVP, quick launch |

---

## How to Use Each

### Absolute Scoring (Default)

```bash
python scripts/demo.py \
  --reference data/reference/cruyff_turn.mp4 \
  --user data/samples/my_attempt.mp4 \
  --skill cruyff_turn
```

**What happens**:
1. Extracts poses from both videos
2. Aligns sequences
3. Measures user's metrics
4. Compares to `config/skills.yaml` ideal values
5. Scores based on deviation from ideal

### Comparative Scoring

```bash
python scripts/demo.py \
  --reference data/reference/cruyff_turn.mp4 \
  --user data/samples/my_attempt.mp4 \
  --skill cruyff_turn \
  --comparative
```

**What happens**:
1. Extracts poses from both videos
2. Aligns sequences
3. Measures **both** reference and user metrics
4. Compares user directly to reference
5. Scores based on percentage deviation from reference

---

## Which Should You Use?

### For MVP / Quick Launch → **Comparative**

- ✅ No calibration needed
- ✅ Works immediately
- ✅ More intuitive for users
- ✅ Reference video = aspirational standard

### For Production / Education → **Absolute**

- ✅ Teaches proper biomechanics
- ✅ Objective standards
- ✅ Can exceed reference performance
- ✅ More scientific/educational

### Hybrid Approach (Future)

Use both:
- **Match Score**: Comparative (how close to reference)
- **Form Score**: Absolute (biomechanical correctness)

---

## Technical Implementation

### Absolute Scoring Flow

```
analyzer.analyze_skill()
  ↓
Extract poses (ref + user)
  ↓
Align sequences (DTW)
  ↓
For each checkpoint:
  - Calculate user's metric value
  - Get ideal_value from config/skills.yaml
  - Score = f(user_value, ideal_value)
  ↓
Overall score = weighted average
```

### Comparative Scoring Flow

```
analyzer.analyze_skill_comparative()
  ↓
Extract poses (ref + user)
  ↓
Align sequences (DTW)
  ↓
For each checkpoint:
  - Calculate ref's metric value
  - Calculate user's metric value
  - deviation = |user - ref| / |ref|
  - Score = f(deviation)
  ↓
Overall score = weighted average
```

---

## Summary

**Absolute Scoring**: "How close are you to the biomechanically perfect form?"
- Uses pre-defined ideals
- Requires calibration
- Educational

**Comparative Scoring**: "How well do you match the reference video?"
- Uses reference as benchmark
- No calibration needed
- Intuitive

Both approaches use the same pose extraction, alignment, and metric calculation. The **only difference** is how they interpret and score the results.

