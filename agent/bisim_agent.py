# Copyright (c) Facebook, Inc. and its affiliates.
# All rights reserved.

# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from copy import deepcopy

import utils
from sac_ae import  Actor, Critic, LOG_FREQ
from transition_model import make_transition_model


class BisimAgent(object):
    """Bisimulation metric algorithm."""
    def __init__(
        self,
        obs_shape,
        action_shape,
        device,
        transition_model_type,
        hidden_dim=256,
        discount=0.99,
        init_temperature=0.01,
        alpha_lr=1e-3,
        alpha_beta=0.9,
        actor_lr=1e-3,
        actor_beta=0.9,
        actor_log_std_min=-10,
        actor_log_std_max=2,
        actor_update_freq=2,
        encoder_stride=2,
        critic_lr=1e-3,
        critic_beta=0.9,
        critic_tau=0.005,
        critic_target_update_freq=2,
        encoder_type='pixel',
        encoder_feature_dim=50,
        encoder_lr=1e-3,
        encoder_tau=0.005,
        decoder_type='pixel',
        decoder_lr=1e-3,
        decoder_update_freq=1,
        decoder_latent_lambda=0.0,
        decoder_weight_lambda=0.0,
        num_layers=4,
        num_filters=32,
        bisim_coef=0.5,
        tr_beta=0.0,
        wb=False,
        run=None
    ):
        self.device = device
        self.discount = discount
        self.critic_tau = critic_tau
        self.encoder_tau = encoder_tau
        self.actor_update_freq = actor_update_freq
        self.critic_target_update_freq = critic_target_update_freq
        self.decoder_update_freq = decoder_update_freq
        self.decoder_latent_lambda = decoder_latent_lambda
        self.transition_model_type = transition_model_type
        self.bisim_coef = bisim_coef
        self.tr_beta = tr_beta
        self.trust_region = False
        if tr_beta > 0.0:
            self.trust_region = True
        if wb:
            import wandb
        self.run = run

        self.actor = Actor(
            obs_shape, action_shape, hidden_dim, encoder_type,
            encoder_feature_dim, actor_log_std_min, actor_log_std_max,
            num_layers, num_filters, encoder_stride
        ).to(device)

        self.critic = Critic(
            obs_shape, action_shape, hidden_dim, encoder_type,
            encoder_feature_dim, num_layers, num_filters, encoder_stride, wb
        ).to(device)

        self.critic_target = Critic(
            obs_shape, action_shape, hidden_dim, encoder_type,
            encoder_feature_dim, num_layers, num_filters, encoder_stride, wb
        ).to(device)

        self.critic_target.load_state_dict(self.critic.state_dict())
        self.prev_encoder = deepcopy(self.critic.encoder)
        self.prev_encoder.eval()
        for p in self.prev_encoder.parameters():
            p.requires_grad = False
        self.prev_encoder_state = deepcopy(self.critic.encoder.state_dict())

        self.transition_model = make_transition_model(
            transition_model_type, encoder_feature_dim, action_shape
        ).to(device)

        self.reward_decoder = nn.Sequential(
            nn.Linear(encoder_feature_dim, 512),
            nn.LayerNorm(512),
            nn.ReLU(),
            nn.Linear(512, 1)).to(device)

        # tie encoders between actor and critic
        self.actor.encoder.copy_conv_weights_from(self.critic.encoder)

        self.log_alpha = torch.tensor(np.log(init_temperature)).to(device)
        self.log_alpha.requires_grad = True
        # set target entropy to -|A|
        self.target_entropy = -np.prod(action_shape)

        # optimizers
        self.actor_optimizer = torch.optim.Adam(
            self.actor.parameters(), lr=actor_lr, betas=(actor_beta, 0.999)
        )

        self.critic_optimizer = torch.optim.Adam(
            self.critic.parameters(), lr=critic_lr, betas=(critic_beta, 0.999)
        )

        self.log_alpha_optimizer = torch.optim.Adam(
            [self.log_alpha], lr=alpha_lr, betas=(alpha_beta, 0.999)
        )

        # optimizer for decoder
        self.decoder_optimizer = torch.optim.Adam(
            list(self.reward_decoder.parameters()) + list(self.transition_model.parameters()),
            lr=decoder_lr,
            weight_decay=decoder_weight_lambda
        )

        # optimizer for critic encoder for reconstruction loss
        self.encoder_optimizer = torch.optim.Adam(
            self.critic.encoder.parameters(), lr=encoder_lr
        )

        self.train()
        self.critic_target.train()

    def train(self, training=True):
        self.training = training
        self.actor.train(training)
        self.critic.train(training)

    @property
    def alpha(self):
        return self.log_alpha.exp()

    def select_action(self, obs):
        with torch.no_grad():
            obs = torch.FloatTensor(obs).to(self.device)
            obs = obs.unsqueeze(0)
            mu, _, _, _ = self.actor(
                obs, compute_pi=False, compute_log_pi=False
            )
            return mu.cpu().data.numpy().flatten()

    def sample_action(self, obs):
        with torch.no_grad():
            obs = torch.FloatTensor(obs).to(self.device)
            obs = obs.unsqueeze(0)
            mu, pi, _, _ = self.actor(obs, compute_log_pi=False)
            return pi.cpu().data.numpy().flatten()

    def update_critic(self, obs, action, reward, next_obs, not_done, L, step):
        with torch.no_grad():
            _, policy_action, log_pi, _ = self.actor(next_obs)
            target_Q1, target_Q2 = self.critic_target(next_obs, policy_action)
            target_V = torch.min(target_Q1,
                                 target_Q2) - self.alpha.detach() * log_pi
            target_Q = reward + (not_done * self.discount * target_V)

        # get current Q estimates
        current_Q1, current_Q2 = self.critic(obs, action, detach_encoder=False)
        q1_loss = F.mse_loss(current_Q1, target_Q) 
        q2_loss = F.mse_loss(current_Q2, target_Q)
        critic_loss = q1_loss + q2_loss
        #L.log('train_critic/loss', critic_loss, step)

        # Optimize the critic
        self.critic_optimizer.zero_grad()
        critic_loss.backward()
        self.critic_optimizer.step()

        self.critic.log(self.run, step)

        if self.run is not None:
            self.run.define_metric("critic/Critic_loss", step_metric="Global_step")
            self.run.log({"critic/Critic_loss": critic_loss,
                        "Global_step":  step})
            
            self.run.define_metric("critic/Q1_value", step_metric="Global_step")
            self.run.define_metric("critic/Q2_value", step_metric="Global_step")
            self.run.define_metric("critic/Q1_loss", step_metric="Global_step")
            self.run.define_metric("critic/Q2_loss", step_metric="Global_step")
            self.run.define_metric("critic/Q_loss", step_metric="Global_step")
            self.run.log(
            {
            "critic/Q1_value": current_Q1.mean().item(),
            "critic/Q2_value": current_Q2.mean().item(),
            "critic/Q1_loss": q1_loss.item(),
            "critic/Q2_loss": q2_loss.item(),
            "Global_step": step
            })


    def update_actor_and_alpha(self, obs, L, step):
        # detach encoder, so we don't update it with the actor loss
        _, pi, log_pi, log_std = self.actor(obs, detach_encoder=True)
        actor_Q1, actor_Q2 = self.critic(obs, pi, detach_encoder=True)

        actor_Q = torch.min(actor_Q1, actor_Q2)
        actor_loss = (self.alpha.detach() * log_pi - actor_Q).mean()

        #L.log('train_actor/loss', actor_loss, step)
        #L.log('train_actor/target_entropy', self.target_entropy, step)
        entropy = 0.5 * log_std.shape[1] * (1.0 + np.log(2 * np.pi)
                                            ) + log_std.sum(dim=-1)
        #L.log('train_actor/entropy', entropy.mean(), step)

        # optimize the actor
        self.actor_optimizer.zero_grad()
        actor_loss.backward()
        self.actor_optimizer.step()

        #self.actor.log(L, step)

        self.log_alpha_optimizer.zero_grad()
        alpha_loss = (self.alpha *
                      (-log_pi - self.target_entropy).detach()).mean()
        #L.log('train_alpha/loss', alpha_loss, step)
        #L.log('train_alpha/value', self.alpha, step)
        alpha_loss.backward()
        self.log_alpha_optimizer.step()

        if self.run is not None:
            self.run.define_metric("actor/Actor_loss", step_metric="Global_step")
            self.run.define_metric("actor/Target_entropy", step_metric="Global_step")
            self.run.define_metric("actor/Entropy", step_metric="Global_step")
            self.run.define_metric("actor/Alpha_loss", step_metric="Global_step")
            self.run.define_metric("actor/Alpha_value", step_metric="Global_step")
            self.run.log({"actor/Actor_loss": actor_loss.item(),
                        "Global_step":  step})
            self.run.log({"actor/Target_entropy": self.target_entropy,
                        "Global_step":  step})
            self.run.log({"actor/Entropy": entropy.mean(),
                        "Global_step":  step})
            self.run.log({"actor/Alpha_loss": alpha_loss.item(),
                        "Global_step":  step})
            self.run.log({"actor/Alpha_value": self.alpha,
                        "Global_step":  step})


    def update_encoder(self, obs, action, reward, L, step):
        #self.prev_encoder_state = deepcopy(self.critic.encoder.state_dict())
        h = self.critic.encoder(obs)
        with torch.no_grad():
            if self.trust_region:
                h_old = self.prev_encoder(obs)

        # Sample random states across episodes at random
        batch_size = obs.size(0)
        perm = np.random.permutation(batch_size)
        h2 = h[perm]

        with torch.no_grad():
            # action, _, _, _ = self.actor(obs, compute_pi=False, compute_log_pi=False)
            pred_next_latent_mu1, pred_next_latent_sigma1 = self.transition_model(torch.cat([h, action], dim=1))
            # reward = self.reward_decoder(pred_next_latent_mu1)
            reward2 = reward[perm]
        if pred_next_latent_sigma1 is None:
            pred_next_latent_sigma1 = torch.zeros_like(pred_next_latent_mu1)
        if pred_next_latent_mu1.ndim == 2:  # shape (B, Z), no ensemble
            pred_next_latent_mu2 = pred_next_latent_mu1[perm]
            pred_next_latent_sigma2 = pred_next_latent_sigma1[perm]
        elif pred_next_latent_mu1.ndim == 3:  # shape (B, E, Z), using an ensemble
            pred_next_latent_mu2 = pred_next_latent_mu1[:, perm]
            pred_next_latent_sigma2 = pred_next_latent_sigma1[:, perm]
        else:
            raise NotImplementedError

        z_dist = F.smooth_l1_loss(h, h2, reduction='none')
        r_dist = F.smooth_l1_loss(reward, reward2, reduction='none')
        if self.transition_model_type == '':
            transition_dist = F.smooth_l1_loss(pred_next_latent_mu1, pred_next_latent_mu2, reduction='none')
        else:
            transition_dist = torch.sqrt(
                (pred_next_latent_mu1 - pred_next_latent_mu2).pow(2) +
                (pred_next_latent_sigma1 - pred_next_latent_sigma2).pow(2)
            )
            # transition_dist  = F.smooth_l1_loss(pred_next_latent_mu1, pred_next_latent_mu2, reduction='none') \
            #     +  F.smooth_l1_loss(pred_next_latent_sigma1, pred_next_latent_sigma2, reduction='none')

        bisimilarity = r_dist + self.discount * transition_dist

        if self.trust_region:
            print("Trust loss")
            trust_loss = F.smooth_l1_loss(h, h_old,reduction='mean')
            loss = (z_dist - bisimilarity).pow(2).mean() + self.tr_beta*trust_loss
            #print("Trust loss", trust_loss.item())
        else:
            print("Bisim loss", )
            loss = (z_dist - bisimilarity).pow(2).mean()
            trust_loss = 0.0
            print("Bisim loss", loss.item())
        #L.log('train_ae/encoder_loss', loss, step)
        if self.run is not None:
            if self.trust_region:
                self.run.define_metric("Trust_region_diff", step_metric="Global_step")
                self.run.log({"Trust_region_diff": trust_loss,
                        "Global_step":  step})
            else:
                latent_diff = self.eval_latent_diff(obs, step, dist_type="l1")
                self.run.define_metric("Trust_region_diff", step_metric = "Global_step")
                self.run.log({"Trust_region_diff": latent_diff,
                    "Global_step": step})
            self.run.define_metric("Reward_dist_loss", step_metric="Global_step")
            self.run.define_metric("Latent_dist_loss", step_metric="Global_step")
            self.run.define_metric("Total_loss", step_metric="Global_step")
            self.run.define_metric("Bisimilarity", step_metric="Global_step")
            self.run.define_metric("Transition_distance", step_metric="Global_step")
            self.run.log({"Reward_dist_loss": r_dist,
                        "Global_step":  step})
            self.run.log({"Latent_dist_loss": z_dist,
                        "Global_step":  step})
            self.run.log({"Total_loss": loss,
                        "Global_step":  step})
            self.run.log({"Bisimilarity": bisimilarity,
                        "Global_step":  step})
            self.run.log({"Transition_distance": transition_dist,
                        "Global_step":  step})
        return loss

    def update_transition_reward_model(self, obs, action, next_obs, reward, L, step):
        h = self.critic.encoder(obs)
        pred_next_latent_mu, pred_next_latent_sigma = self.transition_model(torch.cat([h, action], dim=1))
        if pred_next_latent_sigma is None:
            pred_next_latent_sigma = torch.ones_like(pred_next_latent_mu)

        next_h = self.critic.encoder(next_obs)
        diff = (pred_next_latent_mu - next_h.detach()) / pred_next_latent_sigma
        loss = torch.mean(0.5 * diff.pow(2) + torch.log(pred_next_latent_sigma))
        #L.log('train_ae/transition_loss', loss, step)

        pred_next_latent = self.transition_model.sample_prediction(torch.cat([h, action], dim=1))
        pred_next_reward = self.reward_decoder(pred_next_latent)
        reward_loss = F.mse_loss(pred_next_reward, reward)
        total_loss = loss + reward_loss
        return total_loss

    def update(self, replay_buffer, L, step):
        obs, action, _, reward, next_obs, not_done = replay_buffer.sample()

        #L.log('train/batch_reward', reward.mean(), step)

        self.update_critic(obs, action, reward, next_obs, not_done, L, step)
        transition_reward_loss = self.update_transition_reward_model(obs, action, next_obs, reward, L, step)


        self.prev_encoder_state = deepcopy(self.critic.encoder.state_dict())
        encoder_loss = self.update_encoder(obs, action, reward, L, step)

        total_loss = self.bisim_coef * encoder_loss + transition_reward_loss
        self.encoder_optimizer.zero_grad()
        self.decoder_optimizer.zero_grad()
        total_loss.backward()
        self.encoder_optimizer.step()
        self.decoder_optimizer.step()
            
        self.prev_encoder.load_state_dict(self.prev_encoder_state)

        if step % self.actor_update_freq == 0:
            self.update_actor_and_alpha(obs, L, step)

        if step % self.critic_target_update_freq == 0:
            utils.soft_update_params(
                self.critic.Q1, self.critic_target.Q1, self.critic_tau
            )
            utils.soft_update_params(
                self.critic.Q2, self.critic_target.Q2, self.critic_tau
            )
            utils.soft_update_params(
                self.critic.encoder, self.critic_target.encoder,
                self.encoder_tau
            )

            
    def save(self, model_dir, step):
        torch.save(
            self.actor.state_dict(), '%s/actor_%s.pt' % (model_dir, step)
        )
        torch.save(
            self.critic.state_dict(), '%s/critic_%s.pt' % (model_dir, step)
        )
        torch.save(
            self.reward_decoder.state_dict(),
            '%s/reward_decoder_%s.pt' % (model_dir, step)
        )

    def load(self, model_dir, step):
        self.actor.load_state_dict(
            torch.load('%s/actor_%s.pt' % (model_dir, step))
        )
        self.critic.load_state_dict(
            torch.load('%s/critic_%s.pt' % (model_dir, step))
        )
        self.reward_decoder.load_state_dict(
            torch.load('%s/reward_decoder_%s.pt' % (model_dir, step))
        )

    def eval_latent_diff(self, obs, step, dist_type="l1"):

        h = self.critic.encoder(obs)
        with torch.no_grad():
            h_old = self.prev_encoder(obs)
        
        if dist_type == "l1":
            trust_loss = F.smooth_l1_loss(h, h_old, reduction='mean')

        return trust_loss
        

