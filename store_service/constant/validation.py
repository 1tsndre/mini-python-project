# The size of the VARCHAR(255) columns (emails and names). Longer input has to be rejected up
# front: PostgreSQL would refuse it and the request would end as a 500 instead of a 400.
MAX_VARCHAR_LENGTH = 255
# The longest password bcrypt accepts.
MAX_PASSWORD_BYTES = 72
