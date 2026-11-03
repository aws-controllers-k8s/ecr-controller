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
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	ackv1alpha1 "github.com/aws-controllers-k8s/runtime/apis/core/v1alpha1"
	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackmetrics "github.com/aws-controllers-k8s/runtime/pkg/metrics"
	"github.com/aws/aws-sdk-go-v2/aws"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/ecr"
	"github.com/stretchr/testify/assert"

	svcapitypes "github.com/aws-controllers-k8s/ecr-controller/apis/v1alpha1"
)

func newTagsResource(tags ...*svcapitypes.Tag) *resource {
	return &resource{
		ko: &svcapitypes.Repository{
			Spec: svcapitypes.RepositorySpec{
				Tags: tags,
			},
		},
	}
}

func tag(key, value string) *svcapitypes.Tag {
	return &svcapitypes.Tag{Key: &key, Value: &value}
}

func Test_customPreCompare_Tags(t *testing.T) {
	testCases := []struct {
		name      string
		a         *resource
		b         *resource
		wantDelta bool
	}{
		{
			name:      "no-op",
			a:         newTagsResource(tag("env", "prod"), tag("team", "x")),
			b:         newTagsResource(tag("team", "x"), tag("env", "prod")),
			wantDelta: false,
		},
		{
			name:      "add",
			a:         newTagsResource(tag("env", "prod")),
			b:         newTagsResource(tag("env", "prod"), tag("team", "x")),
			wantDelta: true,
		},
		{
			name:      "remove",
			a:         newTagsResource(tag("env", "prod"), tag("team", "x")),
			b:         newTagsResource(tag("env", "prod")),
			wantDelta: true,
		},
		{
			name:      "value-change",
			a:         newTagsResource(tag("env", "prod")),
			b:         newTagsResource(tag("env", "dev")),
			wantDelta: true,
		},
		// Only checks no-panic on symmetric nil-key inputs; nil-key tags are dropped by convertToOrderedACKTags.
		{
			name:      "symmetric nil-key no-panic",
			a:         newTagsResource(&svcapitypes.Tag{Value: new(string)}),
			b:         newTagsResource(&svcapitypes.Tag{Value: new(string)}),
			wantDelta: false,
		},
		{
			name: "asymmetric nil-key no-panic",
			// b (latest) has env=prod; a (desired) has only a nil-key tag, which convertToOrderedACKTags drops; result is removedTags={env:prod} -> delta expected
			a:         newTagsResource(&svcapitypes.Tag{Value: new(string)}),
			b:         newTagsResource(tag("env", "prod")),
			wantDelta: true,
		},
	}
	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			assert := assert.New(t)
			delta := ackcompare.NewDelta()
			assert.NotPanics(func() {
				customPreCompare(delta, tc.a, tc.b)
			})
			assert.Equal(tc.wantDelta, delta.DifferentAt("Spec.Tags"))
		})
	}
}

type recordedCall struct {
	Op      string
	TagKeys []string
	Tags    map[string]string
}

type recordingHTTPClient struct {
	calls []recordedCall
}

func (r *recordingHTTPClient) Do(req *http.Request) (*http.Response, error) {
	body, _ := io.ReadAll(req.Body)
	var payload struct {
		TagKeys []string
		Tags    []struct {
			Key   string
			Value string
		}
	}
	_ = json.Unmarshal(body, &payload)

	op := req.Header.Get("X-Amz-Target")
	if idx := strings.LastIndex(op, "."); idx >= 0 {
		op = op[idx+1:]
	}

	call := recordedCall{Op: op}
	if len(payload.TagKeys) > 0 {
		call.TagKeys = payload.TagKeys
	}
	if len(payload.Tags) > 0 {
		call.Tags = make(map[string]string, len(payload.Tags))
		for _, t := range payload.Tags {
			call.Tags[t.Key] = t.Value
		}
	}
	r.calls = append(r.calls, call)

	return &http.Response{
		StatusCode: 200,
		Header:     http.Header{"Content-Type": []string{"application/x-amz-json-1.1"}},
		Body:       io.NopCloser(strings.NewReader("{}")),
		Request:    req,
	}, nil
}

func newRecordingResourceManager() (*resourceManager, *recordingHTTPClient) {
	rec := &recordingHTTPClient{}
	client := svcsdk.New(svcsdk.Options{
		Region:      "us-west-2",
		Credentials: aws.AnonymousCredentials{},
		HTTPClient:  rec,
		Retryer:     aws.NopRetryer{},
	})
	return &resourceManager{
		sdkapi:  client,
		metrics: ackmetrics.NewMetrics("ecr"),
	}, rec
}

func withARN(r *resource) *resource {
	arn := ackv1alpha1.AWSResourceName("arn:aws:ecr:us-west-2:123456789012:repository/test")
	r.ko.Status.ACKResourceMetadata = &ackv1alpha1.ResourceMetadata{ARN: &arn}
	return r
}

func Test_syncRepositoryTags(t *testing.T) {
	cases := []struct {
		name      string
		latest    *resource
		desired   *resource
		wantCalls []recordedCall
	}{
		{
			name:      "no-op when tags are identical",
			latest:    newTagsResource(tag("env", "prod"), tag("team", "x")),
			desired:   newTagsResource(tag("team", "x"), tag("env", "prod")),
			wantCalls: nil,
		},
		{
			name:    "add-only: no UntagResource call",
			latest:  newTagsResource(tag("env", "prod")),
			desired: newTagsResource(tag("env", "prod"), tag("team", "x")),
			wantCalls: []recordedCall{
				{Op: "TagResource", Tags: map[string]string{"team": "x"}},
			},
		},
		{
			name:    "value change tags only (upsert, no untag)",
			latest:  newTagsResource(tag("env", "prod")),
			desired: newTagsResource(tag("env", "dev")),
			wantCalls: []recordedCall{
				{Op: "TagResource", Tags: map[string]string{"env": "dev"}},
			},
		},
		{
			name:    "add and remove tags",
			latest:  newTagsResource(tag("env", "prod"), tag("old", "y")),
			desired: newTagsResource(tag("env", "prod"), tag("team", "x")),
			wantCalls: []recordedCall{
				{Op: "UntagResource", TagKeys: []string{"old"}},
				{Op: "TagResource", Tags: map[string]string{"team": "x"}},
			},
		},
		{
			name:    "remove-only: no TagResource call",
			latest:  newTagsResource(tag("env", "prod"), tag("team", "x")),
			desired: newTagsResource(tag("env", "prod")),
			wantCalls: []recordedCall{
				{Op: "UntagResource", TagKeys: []string{"team"}},
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rm, rec := newRecordingResourceManager()
			err := rm.syncRepositoryTags(
				context.Background(),
				withARN(tc.latest),
				tc.desired,
			)
			assert.NoError(t, err)
			assert.Equal(t, tc.wantCalls, rec.calls)
		})
	}
}
