package repository

import (
	"context"

	svcapitypes "github.com/aws-controllers-k8s/ecr-controller/apis/v1alpha1"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	svcsdk "github.com/aws/aws-sdk-go/service/ecr"
)

// setResourceAdditionalFields will describe the fields that are not return by
// DescribeRepository calls
func (rm *resourceManager) setResourceAdditionalFields(
	ctx context.Context,
	r *resource,
	ko *svcapitypes.Repository,
) (err error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.setResourceAdditionalFields")
	defer exit(err)

	getLifecyclePolicyResponse, err := rm.sdkapi.GetLifecyclePolicyWithContext(
		ctx,
		&svcsdk.GetLifecyclePolicyInput{
			RepositoryName: r.ko.Spec.Name,
			RegistryId:     r.ko.Status.RegistryID,
		},
	)
	rm.metrics.RecordAPICall("GET", "GetLifecyclePolicy", err)
	ko.Spec.LifecyclePolicy = getLifecyclePolicyResponse.LifecyclePolicyText
	return nil
}
