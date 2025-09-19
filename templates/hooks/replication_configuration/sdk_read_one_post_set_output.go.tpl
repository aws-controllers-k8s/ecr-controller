	if resp.ReplicationConfiguration != nil {
		ko.Spec.ReplicationConfiguration = rm.crdReplicationConfigurationFromSDK(resp.ReplicationConfiguration)
	} else {
		ko.Spec.ReplicationConfiguration = nil
	}