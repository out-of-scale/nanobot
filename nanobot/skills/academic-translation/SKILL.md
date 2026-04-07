---
name: academic-translation
description: translate pasted english academic prose into high-quality chinese markdown while preserving latex equations, citation markers, symbols, and technical precision. use when the user shares scholarly english text such as abstracts, introductions, methods, results, discussions, theorem-style prose, or figure captions and wants only the chinese translation. useful for terminology-sensitive passages, journal-style writing, and math-heavy text. search authoritative sources only when a term is ambiguous, unusually field-specific, newly coined, or likely to benefit from verification.
---

# Academic Translation

Translate English academic writing into formal, fluent, publication-grade Chinese. Output only the Chinese translation in markdown, with no preface, no notes, no bilingual layout, and no terminology commentary.

## Core behavior

- Translate for meaning, discipline-specific precision, and Chinese academic readability.
- Prefer standard Chinese scholarly terminology used in the relevant field.
- Restructure sentences when needed to avoid literal translation and preserve rigor.
- Maintain a formal, objective, professional tone.
- Preserve the source structure unless a small local reordering is required for natural Chinese syntax.

## Output contract

- Output only the translation.
- Use markdown-compatible plain text so the result can be pasted directly into notes.
- Do not add headings, bullets, numbering, blockquotes, or emphasis unless they already exist in the source or are required to preserve the source structure.
- Do not add explanations such as “以下是译文”, “润色后”, or similar framing.
- Do not provide bilingual comparison, summaries, glossaries, or translator notes.

## Preservation rules

Preserve these elements exactly unless the user explicitly asks otherwise:

- LaTeX delimiters and contents, including inline math and display math
- equation environments such as `\begin{equation}` / `\end{equation}`
- citation markers such as `[12]`, `[3,7]`, `(Smith et al., 2024)`
- mathematical symbols, operators, variable names, and Greek letters
- section numbering, list numbering, and other explicit structural markers
- abbreviations, gene names, dataset names, model names, and other canonical identifiers

Do not translate the contents of formulas. Do not convert LaTeX into unicode math. Do not break markdown or LaTeX syntax.

## Translation workflow

1. Identify the field from the passage and infer the correct academic register.
2. Translate terminology with field-appropriate Chinese equivalents.
3. Rewrite sentence structure where needed to produce natural Chinese academic prose.
4. Check that formulas, citations, symbols, identifiers, and numbering are preserved.
5. Remove any meta commentary so the final answer contains only the translated text.

## When to search

Search only when verification materially improves accuracy, for example:

- a term is ambiguous across fields
- a phrase appears to be a newly coined method, dataset, benchmark, or named theory
- the passage uses niche domain terminology whose standard Chinese rendering is uncertain

When searching:

- prefer authoritative sources such as journal pages, university pages, society glossaries, standards, textbooks, or official project documentation
- use search to confirm terminology, not to rewrite the source content from external sources
- keep the final output citation-free unless citations were already present in the source

## Style rules

- Favor concise, precise, high-information Chinese phrasing.
- Avoid colloquial language and promotional wording.
- Avoid word-for-word calques from English.
- Keep logical relations explicit when the English relies on long nested clauses.
- Preserve the level of certainty in the source: do not overstate tentative claims and do not soften strong claims without evidence.

## Examples

### Example 1

Input:

```text
We show that the proposed estimator is asymptotically unbiased and achieves lower variance than the baseline under mild regularity conditions [12].
```

Output:

```text
我们证明，在适度的正则性条件下，所提出的估计量是渐近无偏的，且其方差低于基线方法 [12]。
```

### Example 2

Input:

```text
For any $\epsilon > 0$, there exists $N \in \mathbb{N}$ such that $|x_n - x| < \epsilon$ for all $n > N$.
```

Output:

```text
对于任意 $\epsilon > 0$，存在 $N \in \mathbb{N}$，使得当所有 $n > N$ 时，均有 $|x_n - x| < \epsilon$。
```

### Example 3

Input:

```text
Recent studies suggest that foundation models can improve zero-shot performance in medical image segmentation, although their robustness remains incompletely characterized.
```

Output:

```text
近期研究表明，基础模型能够提升医学图像分割任务中的零样本性能，但其稳健性仍有待充分刻画。
```
