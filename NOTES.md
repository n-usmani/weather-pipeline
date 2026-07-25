# Notes for Weather Pipeline

## History of the Mental Model
    - Initially, the idea was to have a continuously-accumulating history
    - This meant that the first run would mean no history to compare against
    - INSTEAD: Changed the mental model to have a backward-accumulating history
        - Pull history from the same calendar window in a given city from the last 10 years
        - Compare against current temps in the same city
        - Use this to determine anomalous or not

# Testing
    - Had agent come up with pytests for the code (4+ per function, bullet pointed)
    - Reviewed briefly and gave all-clear for writing and running
    - All 38 tests passed

# Documenting Failures
    - Chronological method of accumulating history was not originally specified
    - Agent took it upon itself to decide on a like-with-like comparison approach, and then switched to a past-30-days comparsion approach
    - I instructed the agent to stick with the like-with-like approach and to add to the spec accordingly