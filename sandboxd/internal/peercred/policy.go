package peercred

func allowedUID(values map[uint32]struct{}, uid uint32) bool {
	_, ok := values[uid]
	return ok
}
