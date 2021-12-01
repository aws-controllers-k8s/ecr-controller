package repository

import (
	"context"

	svcapitypes "github.com/aws-controllers-k8s/ecr-controller/apis/v1alpha1"
	ackerr "github.com/aws-controllers-k8s/runtime/pkg/errors"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	svcsdk "github.com/aws/aws-sdk-go/service/ecr"
)

// setResourceAdditionalFields will describe the fields that are not return by
// DescribeRepository calls
func (rm *resourceManager) setResourceAdditionalFields(
	ctx context.Context,
	ko *svcapitypes.Repository,
) (err error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.setResourceAdditionalFields")
	defer exit(err)

	var getLifecyclePolicyResponse *svcsdk.GetLifecyclePolicyOutput
	getLifecyclePolicyResponse, err = rm.sdkapi.GetLifecyclePolicyWithContext(
		ctx,
		&svcsdk.GetLifecyclePolicyInput{
			RepositoryName: ko.Spec.Name,
			RegistryId:     ko.Spec.RegistryID,
		},
	)
	rm.metrics.RecordAPICall("GET", "GetLifecyclePolicy", err)
	if err != nil {
		if awsErr, ok := ackerr.AWSError(err); ok && awsErr.Code() == svcsdk.ErrCodeLifecyclePolicyNotFoundException {
			ko.Spec.LifecyclePolicy = nil
			return nil
		}
		return err
	}
	if getLifecyclePolicyResponse.LifecyclePolicyText != nil {
		ko.Spec.LifecyclePolicy = getLifecyclePolicyResponse.LifecyclePolicyText
	}
	return nil
}
