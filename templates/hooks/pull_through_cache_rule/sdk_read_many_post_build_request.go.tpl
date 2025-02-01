	if r.ko.Spec.ECRRepositoryPrefix != nil {
		input.EcrRepositoryPrefixes = []string{*r.ko.Spec.ECRRepositoryPrefix}
	}