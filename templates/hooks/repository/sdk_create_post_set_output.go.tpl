	// Repository policy and lifecycle policy need to be set after creation.
	// Return a requeue error to trigger another reconcile loop where these
	// will be handled by the update path, avoiding the issue where a failure
	// in these API calls would leave the repository unmanaged.
	if (ko.Spec.Policy != nil && *ko.Spec.Policy != "") || 
	   (ko.Spec.LifecyclePolicy != nil && *ko.Spec.LifecyclePolicy != "") {
		ackcondition.SetSynced(&resource{ko}, corev1.ConditionFalse, aws.String("bucket created, requeue for updates"), nil)
	}