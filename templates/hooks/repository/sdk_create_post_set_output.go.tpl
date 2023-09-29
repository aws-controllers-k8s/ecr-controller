    // Set the repository policy
	if ko.Spec.Policy != nil && *ko.Spec.Policy != ""  {
		if _, err := rm.updateRepositoryPolicy(ctx, desired); err != nil{
			return nil, err
		}
	}
    // Set the lifecycle policy
	if ko.Spec.LifecyclePolicy != nil && *ko.Spec.LifecyclePolicy != "" {
		if _, err := rm.updateLifecyclePolicy(ctx, desired); err != nil{
			return nil, err
		}
	}