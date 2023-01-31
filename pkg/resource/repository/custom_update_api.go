// Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may
// not use this file except in compliance with the License. A copy of the
// License is located at
//
//     http://aws.amazon.com/apache2.0/
//
// or in the "license" file accompanying this file. This file is distributed
// on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
// express or implied. See the License for the specific language governing
// permissions and limitations under the License.

package repository

import (
	"context"

	"github.com/aws/aws-sdk-go/aws"
	svcsdk "github.com/aws/aws-sdk-go/service/ecr"

	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
)

var (
	defaultImageScanningConfig = svcsdk.ImageScanningConfiguration{
		ScanOnPush: aws.Bool(false),
	}
	defaultImageTagMutability = svcsdk.ImageTagMutabilityMutable
)

// customUpdateRepository implements specialized logic for handling Repository
// resource updates. The ECR API has 4 separate API calls to update a
// Repository, depending on the Repository attribute that has changed:
//
//   - PutImageScanningConfiguration for when the
//     Repository.imageScanningConfiguration struct changed
//   - PutImageTagMutability for when the Repository.imageTagMutability attribute
//     changed
//   - PutLifecyclePolicy for when the Repository.lifecyclePolicy changed
//   - SetRepositoryPolicy for when the Repository.policy changed (yes, it uses
//     "Set" and not "Put"... no idea why this is inconsistent)
func (rm *resourceManager) customUpdateRepository(
	ctx context.Context,
	desired *resource,
	latest *resource,
	delta *ackcompare.Delta,
) (*resource, error) {
	var err error
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.customUpdateRepository")
	defer exit(err)

	var updated *resource
	updated = desired
	if delta.DifferentAt("Spec.ImageScanningConfiguration") {
		updated, err = rm.updateImageScanningConfiguration(ctx, updated)
		if err != nil {
			return nil, err
		}
	}
	if delta.DifferentAt("Spec.ImageTagMutability") {
		updated, err = rm.updateImageTagMutability(ctx, updated)
		if err != nil {
			return nil, err
		}
	}
	if delta.DifferentAt("Spec.LifecyclePolicy") {
		updated, err = rm.updateLifecyclePolicy(ctx, updated)
		if err != nil {
			return nil, err
		}
	}
	if delta.DifferentAt("Spec.Policy") {
		updated, err = rm.updateRepositoryPolicy(ctx, updated)
		if err != nil {
			return nil, err
		}
	}
	if delta.DifferentAt("Spec.Tags") {
		err = rm.syncRepositoryTags(ctx, latest, desired)
		if err != nil {
			return nil, err
		}
	}
	return updated, nil
}

// updateImageScanningConfiguration calls the PutImageScanningConfiguration ECR
// API call for a specific repository
func (rm *resourceManager) updateImageScanningConfiguration(
	ctx context.Context,
	desired *resource,
) (*resource, error) {
	var err error
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.updateImageScanningConfiguration")
	defer exit(err)

	dspec := desired.ko.Spec
	input := &svcsdk.PutImageScanningConfigurationInput{
		RepositoryName: aws.String(*dspec.Name),
	}
	if dspec.ImageScanningConfiguration == nil {
		// There isn't any "reset" behaviour and the image scanning
		// configuration field should always be set...
		input.SetImageScanningConfiguration(&defaultImageScanningConfig)
	} else {
		isc := svcsdk.ImageScanningConfiguration{
			ScanOnPush: dspec.ImageScanningConfiguration.ScanOnPush,
		}
		input.SetImageScanningConfiguration(&isc)
	}
	_, err = rm.sdkapi.PutImageScanningConfigurationWithContext(ctx, input)
	rm.metrics.RecordAPICall("UPDATE", "PutImageScanningConfiguration", err)
	if err != nil {
		return nil, err
	}
	return desired, nil
}

// updateImageTagMutability calls the PutImageTagMutability ECR API call for a
// specific repository
func (rm *resourceManager) updateImageTagMutability(
	ctx context.Context,
	desired *resource,
) (*resource, error) {
	var err error
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.updateImageTagMutability")
	defer exit(err)

	dspec := desired.ko.Spec
	input := &svcsdk.PutImageTagMutabilityInput{
		RepositoryName: aws.String(*dspec.Name),
	}
	if dspec.ImageTagMutability == nil {
		// There isn't any "reset" behaviour and the image scanning
		// configuration field should always be set...
		input.SetImageTagMutability(defaultImageTagMutability)
	} else {
		input.SetImageTagMutability(*dspec.ImageTagMutability)
	}
	_, err = rm.sdkapi.PutImageTagMutabilityWithContext(ctx, input)
	rm.metrics.RecordAPICall("UPDATE", "PutImageTagMutability", err)
	if err != nil {
		return nil, err
	}
	return desired, nil
}

// updateLifecyclePolicy calls the PutLifecyclePolicy ECR API call for a
// specific repository
func (rm *resourceManager) updateLifecyclePolicy(
	ctx context.Context,
	desired *resource,
) (*resource, error) {
	var err error
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.updateRepositoryLifecyclePolicy")
	defer exit(err)

	dspec := desired.ko.Spec

	if dspec.LifecyclePolicy == nil || *dspec.LifecyclePolicy == "" {
		return rm.deleteLifecyclePolicy(ctx, desired)
	}

	input := &svcsdk.PutLifecyclePolicyInput{
		RepositoryName:      dspec.Name,
		RegistryId:          dspec.RegistryID,
		LifecyclePolicyText: dspec.LifecyclePolicy,
	}

	_, err = rm.sdkapi.PutLifecyclePolicyWithContext(ctx, input)
	rm.metrics.RecordAPICall("UPDATE", "PutLifecyclePolicy", err)
	if err != nil {
		return nil, err
	}
	return desired, nil
}

// deleteLifecyclePolicy calls the DeleteLifecyclePolicy ECR API call for a
// specific repository
func (rm *resourceManager) deleteLifecyclePolicy(
	ctx context.Context,
	desired *resource,
) (*resource, error) {
	var err error
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.deleteLifecyclePolicy")
	defer exit(err)

	dspec := desired.ko.Spec
	input := &svcsdk.DeleteLifecyclePolicyInput{
		RepositoryName: dspec.Name,
		RegistryId:     dspec.RegistryID,
	}

	_, err = rm.sdkapi.DeleteLifecyclePolicyWithContext(ctx, input)
	rm.metrics.RecordAPICall("DELETE", "DeleteLifecyclePolicy", err)
	if err != nil {
		return nil, err
	}
	return desired, nil
}

// syncRepositoryTags updates an ECR repository tags.
func (rm *resourceManager) syncRepositoryTags(
	ctx context.Context,
	latest *resource,
	desired *resource,
) (err error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.syncRepositoryTags")
	defer exit(err)

	added, updated, removed := computeTagsDelta(latest.ko.Spec.Tags, desired.ko.Spec.Tags)

	// Tags to create
	added = append(added, updated...)

	if len(removed) > 0 {
		_, err = rm.sdkapi.UntagResourceWithContext(
			ctx,
			&svcsdk.UntagResourceInput{
				ResourceArn: (*string)(latest.ko.Status.ACKResourceMetadata.ARN),
				TagKeys:     removed,
			},
		)
		rm.metrics.RecordAPICall("UPDATE", "UntagResource", err)
		if err != nil {
			return err
		}
	}

	if len(added) > 0 {
		_, err = rm.sdkapi.TagResourceWithContext(
			ctx,
			&svcsdk.TagResourceInput{
				ResourceArn: (*string)(latest.ko.Status.ACKResourceMetadata.ARN),
				Tags:        sdkTagsFromResourceTags(added),
			},
		)
		rm.metrics.RecordAPICall("UPDATE", "TagResource", err)
		if err != nil {
			return err
		}
	}
	return nil
}

// updateRepositoryPolicy updates the policy of a repository
func (rm *resourceManager) updateRepositoryPolicy(
	ctx context.Context,
	desired *resource,
) (*resource, error) {
	var err error
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.updateRepositoryPolicy")
	defer exit(err)

	dspec := desired.ko.Spec

	if dspec.Policy == nil || *dspec.Policy == "" {
		return rm.deleteRepositoryPolicy(ctx, desired)
	}

	input := &svcsdk.SetRepositoryPolicyInput{
		RepositoryName: dspec.Name,
		RegistryId:     dspec.RegistryID,
		PolicyText:     dspec.Policy,
	}

	_, err = rm.sdkapi.SetRepositoryPolicyWithContext(ctx, input)
	rm.metrics.RecordAPICall("UPDATE", "SetRepositoryPolicy", err)
	if err != nil {
		return nil, err
	}
	return desired, nil
}

// deleteRepositoryPolicy deletes a repository policy
func (rm *resourceManager) deleteRepositoryPolicy(
	ctx context.Context,
	desired *resource,
) (*resource, error) {
	var err error
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.deleteRepositoryPolicy")
	defer exit(err)

	dspec := desired.ko.Spec
	input := &svcsdk.DeleteRepositoryPolicyInput{
		RepositoryName: dspec.Name,
		RegistryId:     dspec.RegistryID,
	}

	_, err = rm.sdkapi.DeleteRepositoryPolicyWithContext(ctx, input)
	rm.metrics.RecordAPICall("DELETE", "DeleteRepositoryPolicy", err)
	if err != nil {
		return nil, err
	}
	return desired, nil
}
