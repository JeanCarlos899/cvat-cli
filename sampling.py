def uniform_indices(total, count):
    """Include first and last frame without duplicates."""
    if not 1 <= count <= total:
        raise ValueError("count must be between 1 and the source frame count")
    if count == 1:
        return [0]
    return [round(i * (total - 1) / (count - 1)) for i in range(count)]
