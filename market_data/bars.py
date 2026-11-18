from collections import namedtuple

# The first four fields keep the (date, high, low, close) positions every indicator already reads.
Bar = namedtuple("Bar", "date high low close open volume adj_close")
