"""Task callables referenced by the DAG definitions ("module:function"). Each takes a TaskContext and
returns an Outcome; all state they share lives in the warehouse, not in memory."""
