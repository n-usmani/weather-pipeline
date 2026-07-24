# Notes for Weather Pipeline

## History of the Mental Model
    - Initially, the idea was to have a continuously-accumulating history
    - This meant that the first run would mean no history to compare against
    - INSTEAD: Changed the mental model to have a backward-accumulating history
        - Pull history from the same calendar window in a given city from the last 10 years
        - Compare against current temps in the same city
        - Use this to determine anomalous or not