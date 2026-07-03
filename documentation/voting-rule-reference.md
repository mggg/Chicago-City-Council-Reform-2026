# Voting Rules in the VoteKit Elections Module

This reference file is meant to be used when creating a **config** file - particularly when specifying a list of voting rules to use for a given simulation. The pipeline **expects voting rule values as written below**. Multiple values can be provided within the config file, and the included rules will be used in an equal number of elections. It's always worth double checking that the provided district configuration and seats parameters are aligned with the rules chosen.

## Ranking-based rules
- Plurality — Most first-place votes win
- SNTV — Single non-transferable vote (multi-winner plurality)
- Borda — Positional point scoring
- STV — Single transferable vote (proportional RCV)
- FastSTV — numpy-accelerated STV
- IRV — Instant-runoff (single-winner STV)
- SequentialRCV — Repeated single-winner RCV for several seats
- Alaska — Top-N plurality primary, then IRV
- TopTwo — Runoff between the top two
- RankedPairs — Tideman ranked pairs (Condorcet)
- Schulze — Schulze beatpath method (Condorcet)
- CondoBorda — Condorcet winner if one exists, else Borda
- DominatingSets — Smith / dominating-set method
- PluralityVeto — Plurality with an iterative veto stage
- SerialVeto — Sequential (serial) vetoing
- SimultaneousVeto — Simultaneous vetoing
- RandomDictator — Elect a random ballot's top choice
- BoostedRandomDictator — Boosted/weighted random-dictator variant

## Score-based rules
- Rating — Score/range voting (independent score per candidate)
- Cumulative — Distribute a vote budget, may concentrate on one candidate
- Limited — Fewer votes than seats, at most one per candidate
- GeneralRating — Parent framework for constrained score allocation (base class)

## Approval-based rules
- Approval — Approval voting (one point per approved candidate)
- BlockPlurality — Block voting: approve up to m candidates for m seats
- BlocPlurality — Legacy spelling of the score-based block rule
