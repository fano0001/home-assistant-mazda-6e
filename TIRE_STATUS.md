# Tire status attributes

Tire-pressure sensors show a readable `status`: OK for code 0, low pressure for
code 2, and unknown otherwise. Labels follow the HA server language in English,
Danish or German, with English as fallback. The raw value is in `status_code`.
Automations comparing numeric codes should use `status_code`. The pressure value
is unchanged.
